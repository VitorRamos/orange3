from functools import partial
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

        self.grid = QGridLayout()

        vbox.addLayout(self.grid)

        self.ax = canvas.figure.gca(projection='3d')

        self.layout().addLayout(vbox)
        self.adjustSize()

    @Slot(int)
    def axis_changed(self, ninp, naxis, val):
        self._inputs[ninp]["sel_cols"][naxis] = self._inputs[ninp]["data"].columns[val]
        self.update_plot()

    @Inputs.data
    def set_dataset(self, data, tid=None):
        print(tid, data is None)
        if data is not None:
            if tid in self._inputs:
                slot = self._inputs[tid]
                if list(slot["data"].columns) != list(slot["prev_cols"]):
                    print(list(slot["data"].columns),list(slot["prev_cols"]))
                    for ax in slot["combobox"]:
                        ax.clear()
                        ax.addItems(data.columns)
                        slot["prev_cols"] = slot["data"].columns
                slot["data"] = data
            else:
                axis = [QComboBox(), QComboBox(), QComboBox()]
                labels = [QLabel("X"), QLabel("Y"), QLabel("Z")]
                self._inputs[tid] = {"data": data,
                                     "combobox": axis,
                                     "axis_labels": labels,
                                     "sel_cols": [None, None, None],
                                     "prev_cols": data.columns}
                for i in range(3):
                    axis[i].currentIndexChanged.connect(
                        partial(self.axis_changed, tid, i))
                    ypos = (len(self._inputs)-1)*3
                    self.grid.addWidget(labels[i], 0+ypos, i)
                    self.grid.addWidget(axis[i], 1+ypos, i)

                    axis[i].addItems(data.columns)
        elif tid in self._inputs:
            for ax in self._inputs[tid]["combobox"]:
                ax.deleteLater()
            for axl in self._inputs[tid]["axis_labels"]:
                axl.deleteLater()
            self._inputs.pop(tid)

        self.update_plot()

    def update_plot(self):
        self.ax.clear()
        for v in self._inputs:
            xcol = self._inputs[v]["sel_cols"][0]
            ycol = self._inputs[v]["sel_cols"][1]
            zcol = self._inputs[v]["sel_cols"][2]
            if xcol == None or ycol == None or zcol == None:
                continue
            self.ax.scatter(self._inputs[v]["data"][xcol],
                            self._inputs[v]["data"][ycol],
                            self._inputs[v]["data"][zcol], label=zcol)
        self.ax.legend()
        self.ax.figure.canvas.draw()


if __name__ == "__main__":  # pragma: no cover
    WidgetPreview(OWMatplotlib).run(pd.DataFrame([[1, 3, 3],
                                                  [4, 7, 4],
                                                  [2, 4, 6]],
                                            columns=["col1", "col2", "col3"]))
