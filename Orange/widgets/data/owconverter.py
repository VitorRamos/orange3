from Orange.widgets import widget
from Orange.widgets.widget import OWWidget, Input, Output
from Orange.data.table import Table
from Orange.data.pandas_compat import table_from_frame, table_to_frame
from Orange.widgets.utils.widgetpreview import WidgetPreview

import pandas as pd

class OWConverter(widget.OWWidget):
    name = "Converter"
    description = "convert a dataframe to table"
    icon = "icons/Save.svg"
    category = "Data"

    class Inputs:
        data = Input("Data", pd.DataFrame, multiple=True)

    class Outputs:
        data = Output("Data", Table, default=True)
    
    @Inputs.data
    def set_dataset(self, data, tid=None):
        if data is not None:
            self.Outputs.data.send(table_from_frame(data))
    
    def commit(self):
        self.Outputs.data.send(table_from_frame(self.Inputs.data))

    
if __name__ == "__main__":  # pragma: no cover
    WidgetPreview(OWConverter).run(pd.DataFrame([[1,2,3],
                                                [4,5,6]],
                                                columns=["col1","col2","col3"]))
