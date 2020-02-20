import os.path
import pandas as pd

from AnyQt.QtWidgets import (
    QGridLayout, QRadioButton, QListWidget, 
    QAbstractItemView, QListWidgetItem, QPushButton
)

from Orange.widgets import gui, widget
from Orange.widgets.widget import Input, Output
from Orange.widgets.settings import Setting
from Orange.widgets.utils.widgetpreview import WidgetPreview
from PyQt5.QtCore import pyqtSlot as Slot

_userhome = os.path.expanduser(f"~{os.sep}")

class OWGroupby(widget.OWWidget):
    name = "Groupby"
    description = "A groupby operation involves some combination of splitting"
    "the object, applying a function, and combining the results. This can be "
    "used to group large amounts of data and compute operations on these groups."
    icon = "icons/SelectRows.svg"
    category = "Data"
    keywords = []

    settings_version = 2

    class Information(widget.OWWidget.Information):
        empty_input = widget.Msg("Empty input; nothing was saved.")

    class Error(widget.OWWidget.Error):
        no_file_name = widget.Msg("File name is not set.")
        general_error = widget.Msg("{}")

    class Inputs:
        data = Input("DataFrame", pd.DataFrame)

    class Outputs:
        out_data = Output("Same data Data", pd.DataFrame, default=True)

    add_type_annotations = Setting(True)
    want_main_area = False
    resizing_enabled = False

    filename = Setting("", schema_only=True)
    auto_save = Setting(False)

    def __init__(self):
        super().__init__()
        grid = QGridLayout()
        self.by = QListWidget()
        self.by.setSelectionMode(QAbstractItemView.ExtendedSelection)
        grid.addWidget(self.by, 0, 0)

        self.apply = QListWidget()
        for i in ["min", "max", "mean"]:
            item = QListWidgetItem(i)
            self.apply.addItem(item)
        grid.addWidget(self.apply, 0, 1)

        self.button = QPushButton("Process")
        self.button.clicked.connect(self.process)
        grid.addWidget(self.button, 1, 0)

        selMethBox = gui.vBox(
            self.controlArea, "Select Attributes", addSpace=True)
        selMethBox.layout().addLayout(grid)

        self.adjustSize()

    @Inputs.data
    def dataset(self, data):
        if data is not None:
            self.data = data
            self.by.clear()
            for i in self.data.columns:
                item = QListWidgetItem(f"{i}")
                self.by.addItem(item)
            # self.Outputs.out_data.send(data)

    @Slot()
    def process(self):
        byitems = list(map(lambda x: x.text(), self.by.selectedItems()))
        if len(self.apply.selectedItems()) > 0:
            appitem = self.apply.selectedItems()[0].text()

        df = self.data.groupby(byitems)
        df = getattr(df, appitem)()
        df = df.reset_index()
        self.Outputs.out_data.send(df)

    def send_report(self):
        self.report_data_brief(self.data)


if __name__ == "__main__":  # pragma: no cover
    WidgetPreview(OWGroupby).run(pd.DataFrame([[1,2,3],
                                                [4,5,6]],
                                                columns=["col1","col2","col3"]))
