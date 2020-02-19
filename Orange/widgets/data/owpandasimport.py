"""pandas DataFrame import widget

This module aims to create a widget to import data saved to files from pandas DataFrame.
"""


import os

import pandas as pd

from PyQt5.QtWidgets import (
    QComboBox, QFileDialog, QGridLayout, QLabel, QPushButton, QSizePolicy, QStyle,
)
from PyQt5.QtCore import pyqtSlot as Slot

from Orange.widgets.widget import (OWWidget, Output)
from Orange.widgets.settings import Setting
from Orange.widgets import gui


class OWPandasImport(OWWidget):
    """Widget for importing data from pandas DataFrame formatted files.

    Attributes:
        browse_button: A QPushButton for browsing files.
        file_combo: A QComboBox to display recent and selected files.
    """

    name = "Pandas Import"
    description = "Import a pandas DataFrame data from file."
    icon = "icons/File.svg"
    category = "Data"

    class Outputs:
        data = Output("DataFrame", pd.DataFrame)
    
    dialog_state = Setting({
        "directory": "",
        "filter": ""
    })
    
    want_main_area = False


    def __init__(self):
        super().__init__()

        grid = QGridLayout()

        self.browse_button = QPushButton(
            "…", icon=self.style().standardIcon(QStyle.SP_DirOpenIcon),
            toolTip="Browse files.", autoDefault=False,
        )
        self.browse_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.browse_button.clicked.connect(self.browse)

        self.file_combo = QComboBox(
            self, objectName="recent-combo", toolTip="Recent files.",
            sizeAdjustPolicy=QComboBox.AdjustToMinimumContentsLengthWithIcon,
            minimumContentsLength=16,
        )
        self.file_combo.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Fixed)

        grid.addWidget(QLabel("File:", self), 0, 0, 1, 1)
        grid.addWidget(self.file_combo, 0, 1, 1, 1)
        grid.addWidget(self.browse_button, 0, 2, 1, 1)

        self.controlArea.layout().addLayout(grid)
    

    @Slot()
    def browse(self):
        """Open a file dialog and select a user specified file.

        Supported file types are: csv, xls, pickle.
        """
        
        formats = [
            "All files (*)",
            "Comma-separated values (*.csv)",
            "Microsoft Excel 97-2004 files (.*xls)",
            "Pickle files (*.pkl *.pickle)"
        ]

        dlg = QFileDialog(
            self, windowTitle="Open Data File",
            acceptMode=QFileDialog.AcceptOpen,
            fileMode=QFileDialog.ExistingFile
        )
        dlg.setNameFilters(formats)

        self.recall_last_state(dlg)
        
        status = dlg.exec_()
        dlg.deleteLater()

        if status == QFileDialog.Accepted:
            self.dialog_state["directory"] = dlg.directory().absolutePath()
            self.dialog_state["filter"] = dlg.selectedNameFilter()
            path = dlg.selectedFiles()[0]

            self.file_combo.addItem(path)
            self.load_data()


    def load_data(self):
        path = self.file_combo.currentText()
        file_type = path.rpartition(".")[2]

        if file_type == "csv":
            self.Outputs.data.send(pd.read_csv(path, sep=';'))
        if file_type in ("pkl", "pickle"):
            self.Outputs.data.send(pd.read_pickle(path))
        if file_type == "xls":
            self.Outputs.data.send(pd.read_excel(path))

    

    def recall_last_state(self, file_dialog):
        state = self.dialog_state
        lastdir = state.get("directory", "")
        lastfilter = state.get("filter", "")

        if lastdir and os.path.isdir(lastdir):
            file_dialog.setDirectory(lastdir)
        if lastfilter:
            file_dialog.selectNameFilter(lastfilter)
