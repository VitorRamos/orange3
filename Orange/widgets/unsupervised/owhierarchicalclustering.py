import fractions

from collections import namedtuple, OrderedDict
from itertools import chain
from contextlib import contextmanager

import typing
from typing import Any, List, Tuple, Dict, Optional, Set, Union

import numpy as np

from AnyQt.QtWidgets import (
    QGraphicsWidget, QGraphicsObject, QGraphicsPathItem,
    QGraphicsScene, QGridLayout, QSizePolicy,
    QGraphicsSimpleTextItem, QGraphicsLayoutItem, QAction, QComboBox,
    QGraphicsItemGroup, QGraphicsGridLayout, QGraphicsSceneMouseEvent
)
from AnyQt.QtGui import (
    QTransform, QPainterPath, QPainterPathStroker, QColor, QBrush, QPen,
    QFont, QFontMetrics, QPolygonF, QKeySequence
)
from AnyQt.QtCore import Qt, QSize, QSizeF, QPointF, QRectF, QLineF, QEvent
from AnyQt.QtCore import pyqtSignal as Signal, pyqtSlot as Slot

import pyqtgraph as pg

import Orange.data
from Orange.data.domain import filter_visible
from Orange.data import Domain
import Orange.misc
from Orange.clustering.hierarchical import \
    postorder, preorder, Tree, tree_from_linkage, dist_matrix_linkage, \
    leaves, prune, top_clusters
from Orange.data.util import get_unique_names

from Orange.widgets import widget, gui, settings
from Orange.widgets.utils import colorpalette, itemmodels, combobox
from Orange.widgets.utils.annotated_data import (create_annotated_table,
                                                 ANNOTATED_DATA_SIGNAL_NAME)
from Orange.widgets.utils.widgetpreview import WidgetPreview
from Orange.widgets.widget import Input, Output, Msg

from Orange.widgets.utils.stickygraphicsview import StickyGraphicsView
from Orange.widgets.utils.graphicstextlist import TextListWidget

__all__ = ["OWHierarchicalClustering"]


LINKAGE = ["Single", "Average", "Weighted", "Complete", "Ward"]


def dendrogram_layout(tree, expand_leaves=False):
    # type: (Tree, bool) -> List[Tuple[Tree, Tuple[float, float, float]]]
    coords = []
    cluster_geometry = {}
    leaf_idx = 0
    for node in postorder(tree):
        cluster = node.value
        if node.is_leaf:
            if expand_leaves:
                start = float(cluster.first) + 0.5
                end = float(cluster.last - 1) + 0.5
            else:
                start = end = leaf_idx + 0.5
                leaf_idx += 1
            center = (start + end) / 2.0
            cluster_geometry[node] = (start, center, end)
            coords.append((node, (start, center, end)))
        else:
            left = node.left
            right = node.right
            left_center = cluster_geometry[left][1]
            right_center = cluster_geometry[right][1]
            start, end = left_center, right_center
            center = (start + end) / 2.0
            cluster_geometry[node] = (start, center, end)
            coords.append((node, (start, center, end)))

    return coords


Point = namedtuple("Point", ["x", "y"])
Element = namedtuple("Element", ["anchor", "path"])


def path_toQtPath(geom):
    p = QPainterPath()
    anchor, points = geom
    if len(points) > 1:
        p.moveTo(*points[0])
        for (x, y) in points[1:]:
            p.lineTo(x, y)
    elif len(points) == 1:
        r = QRectF(0, 0, 1e-0, 1e-9)
        r.moveCenter(*points[0])
        p.addRect(r)
    elif len(points) == 0:
        r = QRectF(0, 0, 1e-16, 1e-16)
        r.moveCenter(QPointF(*anchor))
        p.addRect(r)
    return p


#: Dendrogram orientation flags
Left, Top, Right, Bottom = 1, 2, 3, 4


def dendrogram_path(tree, orientation=Left, scaleh=1):
    layout = dendrogram_layout(tree)
    T = {}
    paths = {}
    rootdata = tree.value
    base = scaleh * rootdata.height

    if orientation == Bottom:
        transform = lambda x, y: (x, y)
    if orientation == Top:
        transform = lambda x, y: (x, base - y)
    elif orientation == Left:
        transform = lambda x, y: (base - y, x)
    elif orientation == Right:
        transform = lambda x, y: (y, x)

    for node, (start, center, end) in layout:
        if node.is_leaf:
            x, y = transform(center, 0)
            anchor = Point(x, y)
            paths[node] = Element(anchor, ())
        else:
            left, right = paths[node.left], paths[node.right]
            lines = (left.anchor,
                     Point(*transform(start, scaleh * node.value.height)),
                     Point(*transform(end, scaleh * node.value.height)),
                     right.anchor)
            anchor = Point(*transform(center, scaleh * node.value.height))
            paths[node] = Element(anchor, lines)

        T[node] = Tree((node, paths[node]),
                       tuple(T[ch] for ch in node.branches))
    return T[tree]


def make_pen(brush=Qt.black, width=1, style=Qt.SolidLine,
             cap_style=Qt.SquareCap, join_style=Qt.BevelJoin,
             cosmetic=False):
    pen = QPen(brush)
    pen.setWidth(width)
    pen.setStyle(style)
    pen.setCapStyle(cap_style)
    pen.setJoinStyle(join_style)
    pen.setCosmetic(cosmetic)
    return pen


def update_pen(pen, brush=None, width=None, style=None,
               cap_style=None, join_style=None,
               cosmetic=None):
    pen = QPen(pen)
    if brush is not None:
        pen.setBrush(QBrush(brush))
    if width is not None:
        pen.setWidth(width)
    if style is not None:
        pen.setStyle(style)
    if cap_style is not None:
        pen.setCapStyle(cap_style)
    if join_style is not None:
        pen.setJoinStyle(join_style)
    if cosmetic is not None:
        pen.setCosmetic(cosmetic)
    return pen


def path_stroke(path, width=1, join_style=Qt.MiterJoin):
    stroke = QPainterPathStroker()
    stroke.setWidth(width)
    stroke.setJoinStyle(join_style)
    stroke.setMiterLimit(1.0)
    return stroke.createStroke(path)


def path_outline(path, width=1, join_style=Qt.MiterJoin):
    stroke = path_stroke(path, width, join_style)
    return stroke.united(path)


@contextmanager
def blocked(obj):
    old = obj.signalsBlocked()
    obj.blockSignals(True)
    try:
        yield obj
    finally:
        obj.blockSignals(old)


class DendrogramWidget(QGraphicsWidget):
    """A Graphics Widget displaying a dendrogram."""

    class ClusterGraphicsItem(QGraphicsPathItem):
        #: An extended path describing the full mouse hit area
        #: (extends all the way to the base of the dendrogram)
        mouseAreaShape = QPainterPath()  # type: QPainterPath
        #: The untransformed source path in 'dendrogram' logical coordinate
        #: system
        sourcePath = QPainterPath()  # type: QPainterPath
        sourceAreaShape = QPainterPath()  # type: QPainterPath

        __shape = None  # type: Optional[QPainterPath]
        __boundingRect = None  # type: Optional[QRectF]

        def setGeometryData(self, path, hitArea):
            # type: (QPainterPath, QPainterPath) -> None
            """
            Set the geometry (path) and the mouse hit area (hitArea) for this
            item.
            """
            self.__boundingRect = self.__shape = None
            super().setPath(path)
            assert self.__boundingRect is None, "setPath -> boundingRect"
            assert self.__shape is None, "setPath -> shape"
            self.mouseAreaShape = hitArea

        def shape(self):
            # type: () -> QPainterPath
            if self.__shape is None:
                path = super().shape()  # type: QPainterPath
                self.__shape = path.united(self.mouseAreaShape)
            return self.__shape

        def boundingRect(self):
            # type: () -> QRectF
            if self.__boundingRect is None:
                sh = self.shape()
                pw = self.pen().widthF() / 2.0
                self.__boundingRect = sh.boundingRect().adjusted(-pw, -pw, pw, pw)
            return self.__boundingRect

    class SelectionItem(QGraphicsItemGroup):
        def __init__(self, parent, path, unscaled_path, label=""):
            super().__init__(parent)
            self.path = QGraphicsPathItem(path, self)
            self.path.setPen(make_pen(width=1, cosmetic=True))
            self.addToGroup(self.path)

            self.label = QGraphicsSimpleTextItem(label)
            self._update_label_pos()
            self.addToGroup(self.label)

            self.unscaled_path = unscaled_path

        def set_path(self, path):
            self.path.setPath(path)
            self._update_label_pos()

        def set_label(self, label):
            self.label.setText(label)
            self.label.setBrush(Qt.blue)
            self._update_label_pos()

        def set_color(self, color):
            self.path.setBrush(QColor(color))

        def _update_label_pos(self):
            path = self.path.path()
            elements = (path.elementAt(i) for i in range(path.elementCount()))
            points = ((p.x, p.y) for p in elements)
            p1, p2, *rest = sorted(points)
            x, y = p1[0], (p1[1] + p2[1]) / 2
            brect = self.label.boundingRect()
            # leaf nodes' paths are 4 pixels higher; leafs are `len(rest) == 3`
            self.label.setPos(x - brect.width() - 4,
                              y - brect.height() + 4 * (len(rest) == 3))

    #: Orientation
    Left, Top, Right, Bottom = 1, 2, 3, 4

    #: Selection flags
    NoSelection, SingleSelection, ExtendedSelection = 0, 1, 2

    #: Emitted when a user clicks on the cluster item.
    itemClicked = Signal(ClusterGraphicsItem)
    selectionChanged = Signal()
    selectionEdited = Signal()

    def __init__(self, parent=None, root=None, orientation=Left,
                 hoverHighlightEnabled=True, selectionMode=ExtendedSelection,
                 **kwargs):

        super().__init__(None, **kwargs)
        # Filter all events from children (`ClusterGraphicsItem`s)
        self.setFiltersChildEvents(True)
        self.orientation = orientation
        self._root = None
        #: A tree with dendrogram geometry
        self._layout = None
        self._highlighted_item = None
        #: a list of selected items
        self._selection = OrderedDict()
        #: a {node: item} mapping
        self._items = {}  # type: Dict[Tree, DendrogramWidget.ClusterGraphicsItem]
        #: container for all cluster items.
        self._itemgroup = QGraphicsWidget(self)
        self._itemgroup.setGeometry(self.contentsRect())
        #: Transform mapping from 'dendrogram' to widget local coordinate
        #: system
        self._transform = QTransform()
        self._cluster_parent = {}
        self.__hoverHighlightEnabled = hoverHighlightEnabled
        self.__selectionMode = selectionMode
        self.setContentsMargins(0, 0, 0, 0)
        self.set_root(root)
        if parent is not None:
            self.setParentItem(parent)

    def clear(self):
        scene = self.scene()
        if scene is not None:
            scene.removeItem(self._itemgroup)
        else:
            self._itemgroup.setParentItem(None)
        self._itemgroup = QGraphicsWidget(self)
        self._itemgroup.setGeometry(self.contentsRect())
        self._items.clear()

        for item in self._selection.values():
            if scene is not None:
                scene.removeItem(item)
            else:
                item.setParentItem(None)

        self._root = None
        self._items = {}
        self._selection = OrderedDict()
        self._highlighted_item = None
        self._cluster_parent = {}
        self.updateGeometry()

    def set_root(self, root):
        """Set the root cluster.

        :param Tree root: Root tree.
        """
        self.clear()
        self._root = root
        if root is not None:
            pen = make_pen(Qt.blue, width=1, cosmetic=True,
                           join_style=Qt.MiterJoin)
            for node in postorder(root):
                item = DendrogramWidget.ClusterGraphicsItem(self._itemgroup)
                item.setAcceptHoverEvents(True)
                item.setPen(pen)
                item.node = node
                for branch in node.branches:
                    assert branch in self._items
                    self._cluster_parent[branch] = node
                self._items[node] = item

            self._relayout()
            self._rescale()
        self.updateGeometry()

    def item(self, node):
        """Return the DendrogramNode instance representing the cluster.

        :type cluster: :class:`Tree`

        """
        return self._items.get(node)

    def height_at(self, point):
        """Return the cluster height at the point in widget local coordinates.
        """
        if not self._root:
            return 0
        tinv, ok = self._transform.inverted()
        if not ok:
            return 0
        tpoint = tinv.map(point)
        if self.orientation in [self.Left, self.Right]:
            height = tpoint.x()
        else:
            height = tpoint.y()
        # Undo geometry prescaling
        base = self._root.value.height
        scale = self._height_scale_factor()
        # Use better better precision then double provides.
        Fr = fractions.Fraction
        if scale > 0:
            height = Fr(height) / Fr(scale)
        else:
            height = 0
        if self.orientation in [self.Left, self.Bottom]:
            height = Fr(base) - Fr(height)
        return float(height)

    def pos_at_height(self, height):
        """Return a point in local coordinates for `height` (in cluster
        height scale).
        """
        if not self._root:
            return QPointF()
        scale = self._height_scale_factor()
        base = self._root.value.height
        height = scale * height
        if self.orientation in [self.Left, self.Bottom]:
            height = scale * base - height

        if self.orientation in [self.Left, self.Right]:
            p = QPointF(height, 0)
        else:
            p = QPointF(0, height)
        return self._transform.map(p)

    def _set_hover_item(self, item):
        """Set the currently highlighted item."""
        if self._highlighted_item is item:
            return

        def branches(item):
            return [self._items[ch] for ch in item.node.branches]

        if self._highlighted_item:
            pen = make_pen(Qt.blue, width=1, cosmetic=True)
            for it in postorder(self._highlighted_item, branches):
                it.setPen(pen)

        self._highlighted_item = item
        if item:
            hpen = make_pen(Qt.blue, width=2, cosmetic=True)
            for it in postorder(item, branches):
                it.setPen(hpen)

    def leaf_items(self):
        """Iterate over the dendrogram leaf items (:class:`QGraphicsItem`).
        """
        if self._root:
            return (self._items[leaf] for leaf in leaves(self._root))
        else:
            return iter(())

    def leaf_anchors(self):
        """Iterate over the dendrogram leaf anchor points (:class:`QPointF`).

        The points are in the widget local coordinates.
        """
        for item in self.leaf_items():
            anchor = QPointF(item.element.anchor)
            yield self.mapFromItem(item, anchor)

    def selected_nodes(self):
        """Return the selected clusters."""
        return [item.node for item in self._selection]

    def set_selected_items(self, items):
        """Set the item selection.

        :param items: List of `GraphicsItems`s to select.
        """
        to_remove = set(self._selection) - set(items)
        to_add = set(items) - set(self._selection)

        for sel in to_remove:
            self._remove_selection(sel)
        for sel in to_add:
            self._add_selection(sel)

        if to_add or to_remove:
            self._re_enumerate_selections()
            self.selectionChanged.emit()

    def set_selected_clusters(self, clusters):
        """Set the selected clusters.

        :param Tree items: List of cluster nodes to select .
        """
        self.set_selected_items(list(map(self.item, clusters)))

    def is_selected(self, item):
        return item in self._selection

    def is_included(self, item):
        return self._selected_super_item(item) is not None

    def select_item(self, item, state):
        """Set the `item`s selection state to `select_state`

        :param item: QGraphicsItem.
        :param bool state: New selection state for item.

        """
        if state is False and item not in self._selection or \
                state is True and item in self._selection:
            return  # State unchanged

        if item in self._selection:
            if state is False:
                self._remove_selection(item)
                self._re_enumerate_selections()
                self.selectionChanged.emit()
        else:
            # If item is already inside another selected item,
            # remove that selection
            super_selection = self._selected_super_item(item)

            if super_selection:
                self._remove_selection(super_selection)
            # Remove selections this selection will override.
            sub_selections = self._selected_sub_items(item)

            for sub in sub_selections:
                self._remove_selection(sub)

            if state:
                self._add_selection(item)

            elif item in self._selection:
                self._remove_selection(item)

            self._re_enumerate_selections()
            self.selectionChanged.emit()

    @staticmethod
    def _create_path(item, path):
        ppath = QPainterPath()
        if item.node.is_leaf:
            ppath.addRect(path.boundingRect().adjusted(-8, -4, 0, 4))
        else:
            ppath.addPolygon(path)
            ppath = path_outline(ppath, width=-8)
        return ppath


    @staticmethod
    def _create_label(i):
        return f"C{i + 1}"

    def _add_selection(self, item):
        """Add selection rooted at item
        """
        outline = self._selection_poly(item)
        path = self._transform.map(outline)
        ppath = self._create_path(item, path)
        label = self._create_label(len(self._selection))
        selection_item = self.SelectionItem(self, ppath, outline, label)
        selection_item.setPos(self.contentsRect().topLeft())
        self._selection[item] = selection_item

    def _remove_selection(self, item):
        """Remove selection rooted at item."""

        selection_item = self._selection[item]

        selection_item.hide()
        selection_item.setParentItem(None)
        if self.scene():
            self.scene().removeItem(selection_item)

        del self._selection[item]

    def _selected_sub_items(self, item):
        """Return all selected subclusters under item."""
        def branches(item):
            return [self._items[ch] for ch in item.node.branches]

        res = []
        for item in list(preorder(item, branches))[1:]:
            if item in self._selection:
                res.append(item)
        return res

    def _selected_super_item(self, item):
        """Return the selected super item if it exists."""
        def branches(item):
            return [self._items[ch] for ch in item.node.branches]

        for selected_item in self._selection:
            if item in set(preorder(selected_item, branches)):
                return selected_item
        return None

    def _re_enumerate_selections(self):
        """Re enumerate the selection items and update the colors."""
        # Order the clusters
        items = sorted(self._selection.items(),
                       key=lambda item: item[0].node.value.first)

        palette = colorpalette.ColorPaletteGenerator(len(items))
        for i, (item, selection_item) in enumerate(items):
            # delete and then reinsert to update the ordering
            del self._selection[item]
            self._selection[item] = selection_item
            selection_item.set_label(self._create_label(i))
            color = palette[i]
            color.setAlpha(150)
            selection_item.set_color(color)

    def _selection_poly(self, item):
        # type: (Tree) -> QPolygonF
        """
        Return an selection geometry covering item and all its children.
        """
        def left(item):
            return [self._items[ch] for ch in item.node.branches[:1]]

        def right(item):
            return [self._items[ch] for ch in item.node.branches[-1:]]

        itemsleft = list(preorder(item, left))[::-1]
        itemsright = list(preorder(item, right))
        # itemsleft + itemsright walks from the leftmost leaf up to the root
        # and down to the rightmost leaf
        assert itemsleft[0].node.is_leaf
        assert itemsright[-1].node.is_leaf

        if item.node.is_leaf:
            # a single anchor point
            vert = [itemsleft[0].element.anchor]
        else:
            vert = []
            for it in itemsleft[1:]:
                vert.extend([it.element.path[0], it.element.path[1],
                             it.element.anchor])
            for it in itemsright[:-1]:
                vert.extend([it.element.anchor,
                             it.element.path[-2], it.element.path[-1]])
            # close the polygon
            vert.append(vert[0])

            def isclose(a, b, rel_tol=1e-6):
                return abs(a - b) < rel_tol * max(abs(a), abs(b))

            def isclose_p(p1, p2, rel_tol=1e-6):
                return isclose(p1.x, p2.x, rel_tol) and \
                       isclose(p1.y, p2.y, rel_tol)

            # merge consecutive vertices that are (too) close
            acc = [vert[0]]
            for v in vert[1:]:
                if not isclose_p(v, acc[-1]):
                    acc.append(v)
            vert = acc

        return QPolygonF([QPointF(*p) for p in vert])

    def _update_selection_items(self):
        """Update the shapes of selection items after a scale change.
        """
        transform = self._transform
        for item, selection in self._selection.items():
            path = transform.map(selection.unscaled_path)
            ppath = self._create_path(item, path)
            selection.set_path(ppath)

    def _height_scale_factor(self):
        # Internal dendrogram height scale factor. The dendrogram geometry is
        # scaled by this factor to better condition the geometry
        if self._root is None:
            return 1
        base = self._root.value.height
        # implicitly scale the geometry to 0..1 scale or flush to 0 for fuzz
        if base >= np.finfo(base).eps:
            return 1 / base
        else:
            return 0

    def _relayout(self):
        if self._root is None:
            return

        scale = self._height_scale_factor()
        base = scale * self._root.value.height
        self._layout = dendrogram_path(self._root, self.orientation,
                                       scaleh=scale)
        for node_geom in postorder(self._layout):
            node, geom = node_geom.value
            item = self._items[node]
            item.element = geom
            # the untransformed source path
            item.sourcePath = path_toQtPath(geom)
            r = item.sourcePath.boundingRect()

            if self.orientation == Left:
                r.setRight(base)
            elif self.orientation == Right:
                r.setLeft(0)
            elif self.orientation == Top:
                r.setBottom(base)
            else:
                r.setTop(0)

            hitarea = QPainterPath()
            hitarea.addRect(r)
            item.sourceAreaShape = hitarea
            item.setGeometryData(item.sourcePath, item.sourceAreaShape)
            item.setZValue(-node.value.height)

    def _rescale(self):
        if self._root is None:
            return

        scale = self._height_scale_factor()
        base = scale * self._root.value.height
        crect = self.contentsRect()
        leaf_count = len(list(leaves(self._root)))
        if self.orientation in [Left, Right]:
            drect = QSizeF(base, leaf_count)
        else:
            drect = QSizeF(leaf_count, base)

        eps = np.finfo(np.float64).eps

        if abs(drect.width()) < eps:
            sx = 1.0
        else:
            sx = crect.width() / drect.width()

        if abs(drect.height()) < eps:
            sy = 1.0
        else:
            sy = crect.height() / drect.height()

        transform = QTransform().scale(sx, sy)
        self._transform = transform
        self._itemgroup.setPos(crect.topLeft())
        self._itemgroup.setGeometry(crect)
        for node_geom in postorder(self._layout):
            node, _ = node_geom.value
            item = self._items[node]
            item.setGeometryData(
                transform.map(item.sourcePath),
                transform.map(item.sourceAreaShape)
            )
        self._selection_items = None
        self._update_selection_items()

    def sizeHint(self, which, constraint=QSizeF()):
        fm = QFontMetrics(self.font())
        spacing = fm.lineSpacing()
        mleft, mtop, mright, mbottom = self.getContentsMargins()

        if self._root and which == Qt.PreferredSize:
            nleaves = len([node for node in self._items.keys()
                           if not node.branches])
            base = max(10, min(spacing * 16, 250))
            if self.orientation in [self.Left, self.Right]:
                return QSizeF(base, spacing * nleaves + mleft + mright)
            else:
                return QSizeF(spacing * nleaves + mtop + mbottom, base)

        elif which == Qt.MinimumSize:
            return QSizeF(mleft + mright + 10, mtop + mbottom + 10)
        else:
            return QSizeF()

    def sceneEventFilter(self, obj, event):
        if isinstance(obj, DendrogramWidget.ClusterGraphicsItem):
            if event.type() == QEvent.GraphicsSceneHoverEnter and \
                    self.__hoverHighlightEnabled:
                self._set_hover_item(obj)
                event.accept()
                return True
            elif event.type() == QEvent.GraphicsSceneMousePress and \
                    event.button() == Qt.LeftButton:

                is_selected = self.is_selected(obj)
                is_included = self.is_included(obj)
                current_selection = list(self._selection)

                if self.__selectionMode == DendrogramWidget.SingleSelection:
                    if event.modifiers() & Qt.ControlModifier:
                        self.set_selected_items(
                            [obj] if not is_selected else [])
                    elif event.modifiers() & Qt.AltModifier:
                        self.set_selected_items([])
                    elif event.modifiers() & Qt.ShiftModifier:
                        if not is_included:
                            self.set_selected_items([obj])
                    elif current_selection != [obj]:
                        self.set_selected_items([obj])
                elif self.__selectionMode == DendrogramWidget.ExtendedSelection:
                    if event.modifiers() & Qt.ControlModifier:
                        self.select_item(obj, not is_selected)
                    elif event.modifiers() & Qt.AltModifier:
                        self.select_item(self._selected_super_item(obj), False)
                    elif event.modifiers() & Qt.ShiftModifier:
                        if not is_included:
                            self.select_item(obj, True)
                    elif current_selection != [obj]:
                        self.set_selected_items([obj])

                if current_selection != self._selection:
                    self.selectionEdited.emit()
                self.itemClicked.emit(obj)
                event.accept()
                return True

        if event.type() == QEvent.GraphicsSceneHoverLeave:
            self._set_hover_item(None)

        return super().sceneEventFilter(obj, event)

    def changeEvent(self, event):
        super().changeEvent(event)

        if event.type() == QEvent.FontChange:
            self.updateGeometry()

        # QEvent.ContentsRectChange is missing in PyQt4 <= 4.11.3
        if event.type() == 178:  # QEvent.ContentsRectChange:
            self._rescale()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._rescale()

    def mousePressEvent(self, event):
        QGraphicsWidget.mousePressEvent(self, event)
        # A mouse press on an empty widget part
        if event.modifiers() == Qt.NoModifier and self._selection:
            self.set_selected_clusters([])


class SaveStateSettingsHandler(settings.SettingsHandler):
    """
    A settings handler that delegates session data store/restore to the
    OWWidget instance.

    The OWWidget subclass must implement `save_state() -> Dict[str, Any]` and
    `set_restore_state(state: Dict[str, Any])` methods.
    """
    def initialize(self, instance, data=None):
        super().initialize(instance, data)
        if data is not None and "__session_state_data" in data:
            session_data = data["__session_state_data"]
            instance.set_restore_state(session_data)

    def pack_data(self, widget):
        # type: (widget.OWWidget) -> dict
        res = super().pack_data(widget)
        state = widget.save_state()
        if state:
            assert "__session_state_data" not in res
            res["__session_state_data"] = state
        return res


class _DomainContextHandler(settings.DomainContextHandler,
                            SaveStateSettingsHandler):
    pass


if typing.TYPE_CHECKING:
    #: Encoded selection state for persistent storage.
    #: This is a list of tuples of leaf indices in the selection and
    #: a (N, 3) linkage matrix for validation (the 4-th column from scipy
    #: is omitted).
    SelectionState = Tuple[List[Tuple[int]], List[Tuple[int, int, float]]]


class OWHierarchicalClustering(widget.OWWidget):
    name = "Hierarchical Clustering"
    description = "Display a dendrogram of a hierarchical clustering " \
                  "constructed from the input distance matrix."
    icon = "icons/HierarchicalClustering.svg"
    priority = 2100
    keywords = []

    class Inputs:
        distances = Input("Distances", Orange.misc.DistMatrix)

    class Outputs:
        selected_data = Output("Selected Data", Orange.data.Table, default=True)
        annotated_data = Output(ANNOTATED_DATA_SIGNAL_NAME, Orange.data.Table)

    settingsHandler = _DomainContextHandler()

    #: Selected linkage
    linkage = settings.Setting(1)
    #: Index of the selected annotation item (variable, ...)
    annotation = settings.ContextSetting("Enumeration")
    #: Out-of-context setting for the case when the "Name" option is available
    annotation_if_names = settings.Setting("Name")
    #: Out-of-context setting for the case with just "Enumerate" and "None"
    annotation_if_enumerate = settings.Setting("Enumerate")
    #: Selected tree pruning (none/max depth)
    pruning = settings.Setting(0)
    #: Maximum depth when max depth pruning is selected
    max_depth = settings.Setting(10)

    #: Selected cluster selection method (none, cut distance, top n)
    selection_method = settings.Setting(0)
    #: Cut height ratio wrt root height
    cut_ratio = settings.Setting(75.0)
    #: Number of top clusters to select
    top_n = settings.Setting(3)
    #: Dendrogram zoom factor
    zoom_factor = settings.Setting(0)

    autocommit = settings.Setting(True)

    graph_name = "scene"

    basic_annotations = ["None", "Enumeration"]

    class Error(widget.OWWidget.Error):
        not_finite_distances = Msg("Some distances are infinite")

    #: Stored (manual) selection state (from a saved workflow) to restore.
    __pending_selection_restore = None  # type: Optional[SelectionState]

    def __init__(self):
        super().__init__()

        self.matrix = None
        self.items = None
        self.linkmatrix = None
        self.root = None
        self._displayed_root = None
        self.cutoff_height = 0.0

        gui.comboBox(
            self.controlArea, self, "linkage", items=LINKAGE, box="Linkage",
            callback=self._invalidate_clustering)

        model = itemmodels.VariableListModel()
        model[:] = self.basic_annotations

        box = gui.widgetBox(self.controlArea, "Annotations")
        self.label_cb = cb = combobox.ComboBoxSearch(
            minimumContentsLength=14,
            sizeAdjustPolicy=QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        cb.setModel(model)
        cb.setCurrentIndex(cb.findData(self.annotation, Qt.EditRole))

        def on_annotation_activated():
            self.annotation = cb.currentData(Qt.EditRole)
            self._update_labels()
        cb.activated.connect(on_annotation_activated)

        def on_annotation_changed(value):
            cb.setCurrentIndex(cb.findData(value, Qt.EditRole))
        self.connect_control("annotation", on_annotation_changed)

        box.layout().addWidget(self.label_cb)

        box = gui.radioButtons(
            self.controlArea, self, "pruning", box="Pruning",
            callback=self._invalidate_pruning)
        grid = QGridLayout()
        box.layout().addLayout(grid)
        grid.addWidget(
            gui.appendRadioButton(box, "None", addToLayout=False),
            0, 0
        )
        self.max_depth_spin = gui.spin(
            box, self, "max_depth", minv=1, maxv=100,
            callback=self._invalidate_pruning,
            keyboardTracking=False
        )

        grid.addWidget(
            gui.appendRadioButton(box, "Max depth:", addToLayout=False),
            1, 0)
        grid.addWidget(self.max_depth_spin, 1, 1)

        self.selection_box = gui.radioButtons(
            self.controlArea, self, "selection_method",
            box="Selection",
            callback=self._selection_method_changed)

        grid = QGridLayout()
        self.selection_box.layout().addLayout(grid)
        grid.addWidget(
            gui.appendRadioButton(
                self.selection_box, "Manual", addToLayout=False),
            0, 0
        )
        grid.addWidget(
            gui.appendRadioButton(
                self.selection_box, "Height ratio:", addToLayout=False),
            1, 0
        )
        self.cut_ratio_spin = gui.spin(
            self.selection_box, self, "cut_ratio", 0, 100, step=1e-1,
            spinType=float, callback=self._selection_method_changed
        )
        self.cut_ratio_spin.setSuffix("%")

        grid.addWidget(self.cut_ratio_spin, 1, 1)

        grid.addWidget(
            gui.appendRadioButton(
                self.selection_box, "Top N:", addToLayout=False),
            2, 0
        )
        self.top_n_spin = gui.spin(self.selection_box, self, "top_n", 1, 20,
                                   callback=self._selection_method_changed)
        grid.addWidget(self.top_n_spin, 2, 1)

        self.zoom_slider = gui.hSlider(
            self.controlArea, self, "zoom_factor", box="Zoom",
            minValue=-6, maxValue=3, step=1, ticks=True, createLabel=False,
            callback=self.__update_font_scale)

        zoom_in = QAction(
            "Zoom in", self, shortcut=QKeySequence.ZoomIn,
            triggered=self.__zoom_in
        )
        zoom_out = QAction(
            "Zoom out", self, shortcut=QKeySequence.ZoomOut,
            triggered=self.__zoom_out
        )
        zoom_reset = QAction(
            "Reset zoom", self,
            shortcut=QKeySequence(Qt.ControlModifier | Qt.Key_0),
            triggered=self.__zoom_reset
        )
        self.addActions([zoom_in, zoom_out, zoom_reset])

        self.controlArea.layout().addStretch()

        gui.auto_send(box, self, "autocommit", box=False)

        self.scene = QGraphicsScene()
        self.view = StickyGraphicsView(
            self.scene,
            horizontalScrollBarPolicy=Qt.ScrollBarAlwaysOff,
            verticalScrollBarPolicy=Qt.ScrollBarAlwaysOn,
            alignment=Qt.AlignLeft | Qt.AlignVCenter
        )
        self.mainArea.layout().setSpacing(1)
        self.mainArea.layout().addWidget(self.view)

        def axis_view(orientation):
            ax = AxisItem(orientation=orientation, maxTickLength=7)
            ax.mousePressed.connect(self._activate_cut_line)
            ax.mouseMoved.connect(self._activate_cut_line)
            ax.mouseReleased.connect(self._activate_cut_line)
            ax.setRange(1.0, 0.0)
            return ax

        self.top_axis = axis_view("top")
        self.bottom_axis = axis_view("bottom")

        self._main_graphics = QGraphicsWidget()
        scenelayout = QGraphicsGridLayout()
        scenelayout.setHorizontalSpacing(10)
        scenelayout.setVerticalSpacing(10)

        self._main_graphics.setLayout(scenelayout)
        self.scene.addItem(self._main_graphics)

        self.dendrogram = DendrogramWidget()
        self.dendrogram.setSizePolicy(QSizePolicy.MinimumExpanding,
                                      QSizePolicy.MinimumExpanding)
        self.dendrogram.selectionChanged.connect(self._invalidate_output)
        self.dendrogram.selectionEdited.connect(self._selection_edited)

        self.labels = TextListWidget()
        self.labels.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        self.labels.setAlignment(Qt.AlignLeft)
        self.labels.setMaximumWidth(200)

        scenelayout.addItem(self.top_axis, 0, 0,
                            alignment=Qt.AlignLeft | Qt.AlignVCenter)
        scenelayout.addItem(self.dendrogram, 1, 0,
                            alignment=Qt.AlignLeft | Qt.AlignVCenter)
        scenelayout.addItem(self.labels, 1, 1,
                            alignment=Qt.AlignLeft | Qt.AlignVCenter)
        scenelayout.addItem(self.bottom_axis, 2, 0,
                            alignment=Qt.AlignLeft | Qt.AlignVCenter)
        self.view.viewport().installEventFilter(self)
        self._main_graphics.installEventFilter(self)

        self.top_axis.setZValue(self.dendrogram.zValue() + 10)
        self.bottom_axis.setZValue(self.dendrogram.zValue() + 10)
        self.cut_line = SliderLine(self.top_axis,
                                   orientation=Qt.Horizontal)
        self.cut_line.valueChanged.connect(self._dendrogram_slider_changed)
        self.dendrogram.geometryChanged.connect(self._dendrogram_geom_changed)
        self._set_cut_line_visible(self.selection_method == 1)
        self.__update_font_scale()

    @Inputs.distances
    def set_distances(self, matrix):
        if self.__pending_selection_restore is not None:
            selection_state = self.__pending_selection_restore
        else:
            # save the current selection to (possibly) restore later
            selection_state = self._save_selection()

        self.error()
        self.Error.clear()
        if matrix is not None:
            N, _ = matrix.shape
            if N < 2:
                self.error("Empty distance matrix")
                matrix = None
        if matrix is not None:
            if not np.all(np.isfinite(matrix)):
                self.Error.not_finite_distances()
                matrix = None

        self.matrix = matrix
        if matrix is not None:
            self._set_items(matrix.row_items, matrix.axis)
        else:
            self._set_items(None)
        self._invalidate_clustering()

        # Can now attempt to restore session state from a saved workflow.
        if self.root and selection_state is not None:
            self._restore_selection(selection_state)
            self.__pending_selection_restore = None

        self.unconditional_commit()

    def _set_items(self, items, axis=1):
        self.closeContext()
        self.items = items
        model = self.label_cb.model()
        if len(model) == 3:
            self.annotation_if_names = self.annotation
        elif len(model) == 2:
            self.annotation_if_enumerate = self.annotation
        if isinstance(items, Orange.data.Table) and axis:
            model[:] = chain(
                self.basic_annotations,
                [model.Separator],
                items.domain.class_vars,
                items.domain.metas,
                [model.Separator] if (items.domain.class_vars or items.domain.metas) and
                next(filter_visible(items.domain.attributes), False) else [],
                filter_visible(items.domain.attributes)
            )
            if items.domain.class_vars:
                self.annotation = items.domain.class_vars[0]
            else:
                self.annotation = "Enumeration"
            self.openContext(items.domain)
        else:
            name_option = bool(
                items is not None and (
                    not axis or
                    isinstance(items, list) and
                    all(isinstance(var, Orange.data.Variable) for var in items)))
            model[:] = self.basic_annotations + ["Name"] * name_option
            self.annotation = self.annotation_if_names if name_option \
                else self.annotation_if_enumerate

    def _clear_plot(self):
        self.dendrogram.set_root(None)
        self.labels.setItems([])

    def _set_displayed_root(self, root):
        self._clear_plot()
        self._displayed_root = root
        self.dendrogram.set_root(root)

        self._update_labels()

        self._main_graphics.resize(
            self._main_graphics.size().width(),
            self._main_graphics.sizeHint(Qt.PreferredSize).height()
        )
        self._main_graphics.layout().activate()

    def _update(self):
        self._clear_plot()

        distances = self.matrix

        if distances is not None:
            method = LINKAGE[self.linkage].lower()
            Z = dist_matrix_linkage(distances, linkage=method)

            tree = tree_from_linkage(Z)
            self.linkmatrix = Z
            self.root = tree

            self.top_axis.setRange(tree.value.height, 0.0)
            self.bottom_axis.setRange(tree.value.height, 0.0)

            if self.pruning:
                self._set_displayed_root(prune(tree, level=self.max_depth))
            else:
                self._set_displayed_root(tree)
        else:
            self.linkmatrix = None
            self.root = None
            self._set_displayed_root(None)

        self._apply_selection()

    def _update_labels(self):
        labels = []
        if self.root and self._displayed_root:
            indices = [leaf.value.index for leaf in leaves(self.root)]

            if self.annotation == "None":
                labels = []
            elif self.annotation == "Enumeration":
                labels = [str(i+1) for i in indices]
            elif self.annotation == "Name":
                attr = self.matrix.row_items.domain.attributes
                labels = [str(attr[i]) for i in indices]
            elif isinstance(self.annotation, Orange.data.Variable):
                col_data, _ = self.items.get_column_view(self.annotation)
                labels = [self.annotation.str_val(val) for val in col_data]
                labels = [labels[idx] for idx in indices]
            else:
                labels = []

            if labels and self._displayed_root is not self.root:
                joined = leaves(self._displayed_root)
                labels = [", ".join(labels[leaf.value.first: leaf.value.last])
                          for leaf in joined]

        self.labels.setItems(labels)
        self.labels.setMinimumWidth(1 if labels else -1)

    def _restore_selection(self, state):
        # type: (SelectionState) -> bool
        """
        Restore the (manual) node selection state.

        Return True if successful; False otherwise.
        """
        linkmatrix = self.linkmatrix
        if self.selection_method == 0 and self.root:
            selected, linksaved = state
            linkstruct = np.array(linksaved, dtype=float)
            selected = set(selected)  # type: Set[Tuple[int]]
            if not selected:
                return False
            if linkmatrix.shape[0] != linkstruct.shape[0]:
                return False
            # check that the linkage matrix structure matches. Use isclose for
            # the height column to account for inexact floating point math
            # (e.g. summation order in different ?gemm implementations for
            # euclidean distances, ...)
            if np.any(linkstruct[:, :2] != linkmatrix[:, :2]) or \
                    not np.all(np.isclose(linkstruct[:, 2], linkstruct[:, 2])):
                return False
            selection = []
            indices = np.array([n.value.index for n in leaves(self.root)],
                               dtype=int)
            # mapping from ranges to display (pruned) nodes
            mapping = {node.value.range: node
                       for node in postorder(self._displayed_root)}
            for node in postorder(self.root):  # type: Tree
                r = tuple(indices[node.value.first: node.value.last])
                if r in selected:
                    if node.value.range not in mapping:
                        # the node was pruned from display and cannot be
                        # selected
                        break
                    selection.append(mapping[node.value.range])
                    selected.remove(r)
                if not selected:
                    break  # found all, nothing more to do
            if selection and selected:
                # Could not restore all selected nodes (only partial match)
                return False

            self._set_selected_nodes(selection)
            return True
        return False

    def _set_selected_nodes(self, selection):
        # type: (List[Tree]) -> None
        """
        Set the nodes in `selection` to be the current selected nodes.

        The selection nodes must be subtrees of the current `_displayed_root`.
        """
        self.dendrogram.selectionChanged.disconnect(self._invalidate_output)
        try:
            self.dendrogram.set_selected_clusters(selection)
        finally:
            self.dendrogram.selectionChanged.connect(self._invalidate_output)

    def _invalidate_clustering(self):
        self._update()
        self._update_labels()
        self._invalidate_output()

    def _invalidate_output(self):
        self.commit()

    def _invalidate_pruning(self):
        if self.root:
            selection = self.dendrogram.selected_nodes()
            ranges = [node.value.range for node in selection]
            if self.pruning:
                self._set_displayed_root(
                    prune(self.root, level=self.max_depth))
            else:
                self._set_displayed_root(self.root)
            selected = [node for node in preorder(self._displayed_root)
                        if node.value.range in ranges]

            self.dendrogram.set_selected_clusters(selected)

        self._apply_selection()

    def commit(self):
        items = getattr(self.matrix, "items", self.items)
        if not items:
            self.Outputs.selected_data.send(None)
            self.Outputs.annotated_data.send(None)
            return

        selection = self.dendrogram.selected_nodes()
        selection = sorted(selection, key=lambda c: c.value.first)

        indices = [leaf.value.index for leaf in leaves(self.root)]

        maps = [indices[node.value.first:node.value.last]
                for node in selection]

        selected_indices = list(chain(*maps))
        unselected_indices = sorted(set(range(self.root.value.last)) -
                                    set(selected_indices))

        if not selected_indices:
            self.Outputs.selected_data.send(None)
            annotated_data = create_annotated_table(items, []) \
                if self.selection_method == 0 and self.matrix.axis else None
            self.Outputs.annotated_data.send(annotated_data)
            return

        selected_data = None

        if isinstance(items, Orange.data.Table) and self.matrix.axis == 1:
            # Select rows
            c = np.zeros(self.matrix.shape[0])

            for i, indices in enumerate(maps):
                c[indices] = i
            c[unselected_indices] = len(maps)

            mask = c != len(maps)

            data, domain = items, items.domain
            attrs = domain.attributes
            classes = domain.class_vars
            metas = domain.metas

            var_name = get_unique_names(domain, "Cluster")
            values = [f"C{i + 1}" for i in range(len(maps))]

            clust_var = Orange.data.DiscreteVariable(
                var_name, values=values + ["Other"])
            domain = Orange.data.Domain(attrs, classes, metas + (clust_var,))
            data = items.transform(domain)
            data.get_column_view(clust_var)[0][:] = c

            if selected_indices:
                selected_data = data[mask]
                clust_var = Orange.data.DiscreteVariable(
                    var_name, values=values)
                selected_data.domain = Domain(
                    attrs, classes, metas + (clust_var, ))

        elif isinstance(items, Orange.data.Table) and self.matrix.axis == 0:
            # Select columns
            domain = Orange.data.Domain(
                [items.domain[i] for i in selected_indices],
                items.domain.class_vars, items.domain.metas)
            selected_data = items.from_table(domain, items)
            data = None

        self.Outputs.selected_data.send(selected_data)
        annotated_data = create_annotated_table(data, selected_indices)
        self.Outputs.annotated_data.send(annotated_data)

    def sizeHint(self):
        return QSize(800, 500)

    def eventFilter(self, obj, event):
        if obj is self.view.viewport() and event.type() == QEvent.Resize:
            # NOTE: not using viewport.width(), due to 'transient' scroll bars
            # (macOS). Viewport covers the whole view, but QGraphicsView still
            # scrolls left, right with scroll bar extent (other
            # QAbstractScrollArea widgets behave as expected).
            w_frame = self.view.frameWidth()
            margin = self.view.viewportMargins()
            w_scroll = self.view.verticalScrollBar().width()
            width = (self.view.width() - w_frame * 2 -
                     margin.left() - margin.right() - w_scroll)
            # layout with new width constraint
            self.__layout_main_graphics(width=width)
        elif obj is self._main_graphics and \
                event.type() == QEvent.LayoutRequest:
            # layout preserving the width (vertical re layout)
            self.__layout_main_graphics()
        return super().eventFilter(obj, event)

    @Slot(QPointF)
    def _activate_cut_line(self, pos: QPointF):
        """Activate cut line selection an set cut value to `pos.x()`."""
        self.selection_method = 1
        self.cut_line.setValue(pos.x())
        self._selection_method_changed()

    def onDeleteWidget(self):
        super().onDeleteWidget()
        self._clear_plot()
        self.dendrogram.clear()
        self.dendrogram.deleteLater()

    def _dendrogram_geom_changed(self):
        pos = self.dendrogram.pos_at_height(self.cutoff_height)
        geom = self.dendrogram.geometry()
        self._set_slider_value(pos.x(), geom.width())

        self.cut_line.setLength(
            self.bottom_axis.geometry().bottom()
            - self.top_axis.geometry().top()
        )

        geom = self._main_graphics.geometry()
        assert geom.topLeft() == QPointF(0, 0)

        def adjustLeft(rect):
            rect = QRectF(rect)
            rect.setLeft(geom.left())
            return rect
        margin = 3
        self.view.setSceneRect(geom)
        self.view.setHeaderSceneRect(
            adjustLeft(self.top_axis.geometry()).adjusted(0, 0, 0, margin)
        )
        self.view.setFooterSceneRect(
            adjustLeft(self.bottom_axis.geometry()).adjusted(0, -margin, 0, 0)
        )

    def _dendrogram_slider_changed(self, value):
        p = QPointF(value, 0)
        cl_height = self.dendrogram.height_at(p)

        self.set_cutoff_height(cl_height)

    def _set_slider_value(self, value, span):
        with blocked(self.cut_line):
            self.cut_line.setRange(0, span)
            self.cut_line.setValue(value)

    def set_cutoff_height(self, height):
        self.cutoff_height = height
        if self.root:
            self.cut_ratio = 100 * height / self.root.value.height
        self.select_max_height(height)

    def _set_cut_line_visible(self, visible):
        self.cut_line.setVisible(visible)

    def select_top_n(self, n):
        root = self._displayed_root
        if root:
            clusters = top_clusters(root, n)
            self.dendrogram.set_selected_clusters(clusters)

    def select_max_height(self, height):
        root = self._displayed_root
        if root:
            clusters = clusters_at_height(root, height)
            self.dendrogram.set_selected_clusters(clusters)

    def _selection_method_changed(self):
        self._set_cut_line_visible(self.selection_method == 1)
        if self.root:
            self._apply_selection()

    def _apply_selection(self):
        if not self.root:
            return

        if self.selection_method == 0:
            pass
        elif self.selection_method == 1:
            height = self.cut_ratio * self.root.value.height / 100
            self.set_cutoff_height(height)
            pos = self.dendrogram.pos_at_height(height)
            self._set_slider_value(pos.x(), self.dendrogram.size().width())
        elif self.selection_method == 2:
            self.select_top_n(self.top_n)

    def _selection_edited(self):
        # Selection was edited by clicking on a cluster in the
        # dendrogram view.
        self.selection_method = 0
        self._selection_method_changed()
        self._invalidate_output()

    def _save_selection(self):
        # Save the current manual node selection state
        selection_state = None
        if self.selection_method == 0 and self.root:
            assert self.linkmatrix is not None
            linkmat = [(int(_0), int(_1), _2)
                       for _0, _1, _2 in self.linkmatrix[:, :3].tolist()]
            nodes_ = self.dendrogram.selected_nodes()
            # match the display (pruned) nodes back (by ranges)
            mapping = {node.value.range: node for node in postorder(self.root)}
            nodes = [mapping[node.value.range] for node in nodes_]
            indices = [tuple(node.value.index for node in leaves(node))
                       for node in nodes]
            if nodes:
                selection_state = (indices, linkmat)
        return selection_state

    def save_state(self):
        # type: () -> Dict[str, Any]
        """
        Save state for `set_restore_state`
        """
        selection = self._save_selection()
        res = {"version": (0, 0, 0)}
        if selection is not None:
            res["selection_state"] = selection
        return res

    def set_restore_state(self, state):
        # type: (Dict[str, Any]) -> bool
        """
        Restore session data from a saved state.

        Parameters
        ----------
        state : Dict[str, Any]

        NOTE
        ----
        This is method called while the instance (self) is being constructed,
        even before its `__init__` is called. Consider `self` to be only a
        `QObject` at this stage.
        """
        if "selection_state" in state:
            selection = state["selection_state"]
            self.__pending_selection_restore = selection
        return True

    def __zoom_in(self):
        def clip(minval, maxval, val):
            return min(max(val, minval), maxval)
        self.zoom_factor = clip(self.zoom_slider.minimum(),
                                self.zoom_slider.maximum(),
                                self.zoom_factor + 1)
        self.__update_font_scale()

    def __zoom_out(self):
        def clip(minval, maxval, val):
            return min(max(val, minval), maxval)
        self.zoom_factor = clip(self.zoom_slider.minimum(),
                                self.zoom_slider.maximum(),
                                self.zoom_factor - 1)
        self.__update_font_scale()

    def __zoom_reset(self):
        self.zoom_factor = 0
        self.__update_font_scale()

    def __layout_main_graphics(self, width=-1):
        if width < 0:
            # Preserve current width.
            width = self._main_graphics.size().width()
        preferred = self._main_graphics.effectiveSizeHint(
            Qt.PreferredSize, constraint=QSizeF(width, -1))
        self._main_graphics.resize(QSizeF(width, preferred.height()))
        mw = self._main_graphics.minimumWidth() + 4
        self.view.setMinimumWidth(mw + self.view.verticalScrollBar().width())

    def __update_font_scale(self):
        font = self.scene.font()
        factor = (1.25 ** self.zoom_factor)
        font = qfont_scaled(font, factor)
        self._main_graphics.setFont(font)

    def send_report(self):
        annot = self.label_cb.currentText()
        if isinstance(self.annotation, str):
            annot = annot.lower()
        if self.selection_method == 0:
            sel = "manual"
        elif self.selection_method == 1:
            sel = "at {:.1f} of height".format(self.cut_ratio)
        else:
            sel = "top {} clusters".format(self.top_n)
        self.report_items((
            ("Linkage", LINKAGE[self.linkage].lower()),
            ("Annotation", annot),
            ("Prunning",
             self.pruning != 0 and "{} levels".format(self.max_depth)),
            ("Selection", sel),
        ))
        self.report_plot()


def qfont_scaled(font, factor):
    scaled = QFont(font)
    if font.pointSizeF() != -1:
        scaled.setPointSizeF(font.pointSizeF() * factor)
    elif font.pixelSize() != -1:
        scaled.setPixelSize(int(font.pixelSize() * factor))
    return scaled


class WrapperLayoutItem(QGraphicsLayoutItem):
    """A Graphics layout item wrapping a QGraphicsItem allowing it
    to be managed by a layout.
    """
    def __init__(self, item, orientation=Qt.Horizontal, parent=None):
        QGraphicsLayoutItem.__init__(self, parent)
        self.orientation = orientation
        self.item = item
        if orientation == Qt.Vertical:
            self.item.setRotation(-90)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        else:
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

    def setGeometry(self, rect):
        QGraphicsLayoutItem.setGeometry(self, rect)
        if self.orientation == Qt.Horizontal:
            self.item.setPos(rect.topLeft())
        else:
            self.item.setPos(rect.bottomLeft())

    def sizeHint(self, which, constraint=QSizeF()):
        if which == Qt.PreferredSize:
            size = self.item.boundingRect().size()
            if self.orientation == Qt.Horizontal:
                return size
            else:
                return QSizeF(size.height(), size.width())
        else:
            return QSizeF()

    def setFont(self, font):
        self.item.setFont(font)
        self.updateGeometry()

    def setText(self, text):
        self.item.setText(text)
        self.updateGeometry()

    def setToolTip(self, tip):
        self.item.setToolTip(tip)


class AxisItem(pg.AxisItem):
    mousePressed = Signal(QPointF, Qt.MouseButton)
    mouseMoved = Signal(QPointF, Qt.MouseButtons)
    mouseReleased = Signal(QPointF, Qt.MouseButton)

    #: \reimp
    def wheelEvent(self, event):
        event.ignore()  # ignore event to propagate to the view -> scroll

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        self.mousePressed.emit(event.pos(), event.button())
        super().mousePressEvent(event)
        event.accept()

    def mouseMoveEvent(self, event):
        self.mouseMoved.emit(event.pos(), event.buttons())
        super().mouseMoveEvent(event)
        event.accept()

    def mouseReleaseEvent(self, event):
        self.mouseReleased.emit(event.pos(), event.button())
        super().mouseReleaseEvent(event)
        event.accept()


class SliderLine(QGraphicsObject):
    """A movable slider line."""
    valueChanged = Signal(float)

    linePressed = Signal()
    lineMoved = Signal()
    lineReleased = Signal()
    rangeChanged = Signal(float, float)

    def __init__(self, parent=None, orientation=Qt.Vertical, value=0.0,
                 length=10.0, **kwargs):
        self._orientation = orientation
        self._value = value
        self._length = length
        self._min = 0.0
        self._max = 1.0
        self._line = QLineF()  # type: Optional[QLineF]
        self._pen = QPen()
        super().__init__(parent, **kwargs)

        self.setAcceptedMouseButtons(Qt.LeftButton)
        self.setPen(make_pen(brush=QColor(50, 50, 50), width=1, cosmetic=False,
                             style=Qt.DashLine))

        if self._orientation == Qt.Vertical:
            self.setCursor(Qt.SizeVerCursor)
        else:
            self.setCursor(Qt.SizeHorCursor)

    def setPen(self, pen: Union[QPen, Qt.GlobalColor, Qt.PenStyle]) -> None:
        pen = QPen(pen)
        if self._pen != pen:
            self.prepareGeometryChange()
            self._pen = pen
            self._line = None
            self.update()

    def pen(self) -> QPen:
        return QPen(self._pen)

    def setValue(self, value: float):
        value = min(max(value, self._min), self._max)

        if self._value != value:
            self.prepareGeometryChange()
            self._value = value
            self._line = None
            self.valueChanged.emit(value)

    def value(self) -> float:
        return self._value

    def setRange(self, minval: float, maxval: float) -> None:
        maxval = max(minval, maxval)
        if minval != self._min or maxval != self._max:
            self._min = minval
            self._max = maxval
            self.rangeChanged.emit(minval, maxval)
            self.setValue(self._value)

    def setLength(self, length: float):
        if self._length != length:
            self.prepareGeometryChange()
            self._length = length
            self._line = None

    def length(self) -> float:
        return self._length

    def setOrientation(self, orientation: Qt.Orientation):
        if self._orientation != orientation:
            self.prepareGeometryChange()
            self._orientation = orientation
            self._line = None
            if self._orientation == Qt.Vertical:
                self.setCursor(Qt.SizeVerCursor)
            else:
                self.setCursor(Qt.SizeHorCursor)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        event.accept()
        self.linePressed.emit()

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        pos = event.pos()
        if self._orientation == Qt.Vertical:
            self.setValue(pos.y())
        else:
            self.setValue(pos.x())
        self.lineMoved.emit()
        event.accept()

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:
        if self._orientation == Qt.Vertical:
            self.setValue(event.pos().y())
        else:
            self.setValue(event.pos().x())
        self.lineReleased.emit()
        event.accept()

    def boundingRect(self) -> QRectF:
        if self._line is None:
            if self._orientation == Qt.Vertical:
                self._line = QLineF(0, self._value, self._length, self._value)
            else:
                self._line = QLineF(self._value, 0, self._value, self._length)
        r = QRectF(self._line.p1(), self._line.p2())
        penw = self.pen().width()
        return r.adjusted(-penw, -penw, penw, penw)

    def paint(self, painter, *args):
        if self._line is None:
            self.boundingRect()

        painter.save()
        painter.setPen(self.pen())
        painter.drawLine(self._line)
        painter.restore()


def clusters_at_height(root, height):
    """Return a list of clusters by cutting the clustering at `height`.
    """
    lower = set()
    cluster_list = []
    for cl in preorder(root):
        if cl in lower:
            continue
        if cl.value.height < height:
            cluster_list.append(cl)
            lower.update(preorder(cl))
    return cluster_list


if __name__ == "__main__":  # pragma: no cover
    from Orange import distance
    data = Orange.data.Table("iris")
    matrix = distance.Euclidean(distance._preprocess(data))
    WidgetPreview(OWHierarchicalClustering).run(matrix)
