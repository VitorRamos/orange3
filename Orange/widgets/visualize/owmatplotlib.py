from collections import OrderedDict
import pandas as pd
from Orange.widgets.utils.widgetpreview import WidgetPreview
from Orange.widgets.widget import Input

from AnyQt.QtWidgets import (
    QVBoxLayout,
    QComboBox, QLabel,
    QGridLayout, QRadioButton, QListWidget,
    QAbstractItemView, QListWidgetItem, QPushButton
)

from Orange.widgets import widget
from Orange.widgets.widget import Input
from Orange.widgets.settings import Setting
from PyQt5.QtCore import pyqtSlot as Slot

from matplotlib.figure import Figure
from mpl_toolkits.mplot3d import Axes3D
from matplotlib.backends.qt_compat import QtCore, QtWidgets, is_pyqt5
if is_pyqt5():
    from matplotlib.backends.backend_qt5agg import (
        FigureCanvas, NavigationToolbar2QT as NavigationToolbar)
else:
    from matplotlib.backends.backend_qt4agg import (
        FigureCanvas, NavigationToolbar2QT as NavigationToolbar)


class OWMatplotlib(widget.OWWidget):
    name = "3D Plot"
    description = "plot using matplotlib"
    icon = "icons/Save.svg"
    category = "visualize"

    class Inputs:
        data = Input("DataFrame", pd.DataFrame, multiple=True)

    def __init__(self):
        super().__init__()

        self._inputs = OrderedDict()

        vbox = QVBoxLayout()

        canvas = FigureCanvas(Figure(figsize=(5, 5)))
        vbox.addWidget(canvas)

        grid = QGridLayout()

        self.combo_xaxis = QComboBox()
        self.combo_xaxis.currentIndexChanged.connect(self.x_axis_change)
        grid.addWidget(QLabel("Xaxis"), 0, 0)
        grid.addWidget(self.combo_xaxis, 1, 0)
        self.sel_xcol = None

        self.combo_yaxis = QComboBox()
        self.combo_yaxis.currentIndexChanged.connect(self.y_axis_change)
        grid.addWidget(QLabel("Yaxis"), 0, 1)
        grid.addWidget(self.combo_yaxis, 1, 1)
        self.sel_ycol = None

        self.combo_zaxis = QComboBox()
        self.combo_zaxis.currentIndexChanged.connect(self.z_axis_change)
        grid.addWidget(QLabel("Zaxis"), 0, 2)
        grid.addWidget(self.combo_zaxis, 1, 2)
        self.sel_zcol = None

        vbox.addLayout(grid)

        self.ax = canvas.figure.gca(projection='3d')

        self.layout().addLayout(vbox)
        self.adjustSize()
        self.prev_cols = []


    @Slot(int)
    def x_axis_change(self, val):
        self.sel_xcol = self.prev_cols[val]
        self.update_plot()

    @Slot(int)
    def y_axis_change(self, val):
        self.sel_ycol = self.prev_cols[val]
        self.update_plot()

    @Slot(int)
    def z_axis_change(self, val):
        self.sel_zcol = self.prev_cols[val]
        self.update_plot()

    @Inputs.data
    def set_dataset(self, data, tid=None):
        if data is not None:
            for v in self._inputs:
                if list(self._inputs[v].columns) != list(data.columns):
                    raise Exception("AAAA")
                
            if list(self.prev_cols) != list(data.columns):
                self.combo_xaxis.clear()
                self.combo_yaxis.clear()
                self.combo_zaxis.clear()

                self.prev_cols = data.columns
                self.combo_xaxis.addItems(data.columns)
                self.combo_yaxis.addItems(data.columns)
                self.combo_zaxis.addItems(data.columns)
                
            self._inputs[tid] = data
            self.update_plot()
        elif tid in self._inputs:
            self._inputs.pop(tid)
            self.update_plot()

    def update_plot(self):
        if not self.sel_xcol or not self.sel_ycol or not self.sel_zcol:
            return
        self.ax.clear()
        for v in self._inputs:
            self.ax.scatter(self._inputs[v][self.sel_xcol],
                            self._inputs[v][self.sel_ycol],
                            self._inputs[v][self.sel_zcol])
        self.ax.figure.canvas.draw()


if __name__ == "__main__":  # pragma: no cover
    WidgetPreview(OWMatplotlib).run(pd.DataFrame([[1, 3, 3],
                                                  [4, 7, 6],
                                                  [2, 4, 6]],
                                                 columns=["col1", "col2", "col3"]))
