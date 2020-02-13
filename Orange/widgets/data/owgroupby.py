import os.path

from AnyQt.QtWidgets import (
    QFileDialog, QGridLayout, QMessageBox,
    QTableView, QRadioButton, QButtonGroup, QGridLayout,
    QStackedWidget, QHeaderView, QCheckBox, QItemDelegate,
)

from Orange.data.table import Table
from Orange.data.io import TabReader
from Orange.widgets import gui, widget
from Orange.widgets.widget import Input, Output
from Orange.widgets.settings import Setting
from Orange.widgets.utils.widgetpreview import WidgetPreview


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
        data = Input("Data", Table)

    class Outputs:
        out_data = Output("Same data Data", Table, default=True)

    add_type_annotations = Setting(True)
    want_main_area = False
    resizing_enabled = False

    filename = Setting("", schema_only=True)
    auto_save = Setting(False)

    def __init__(self):
        super().__init__()
        grid = QGridLayout()
        b = QRadioButton("AAAAA")
        grid.addWidget(b, 0, 0)

        selMethBox = gui.vBox(self.controlArea, "Select Attributes", addSpace=True)
        selMethBox.layout().addLayout(grid)

        self.adjustSize()

    @Inputs.data
    def dataset(self, data):
        self.data = data
        self.data.domain
        self.Outputs.out_data.send(data)

    def send_report(self):
        self.report_data_brief(self.data)


if __name__ == "__main__":  # pragma: no cover
    WidgetPreview(OWSave).run(Table("iris"))
