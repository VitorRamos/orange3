import inspect
from Orange.widgets import gui, widget
from Orange.widgets.widget import Input, Output

class Variable:
    def __init__(self, dtype, v=None, desc=""):
        self.desc = desc
        self.type = dtype
        self.v = v

    def __repr__(self):
        if self.type == "Obrigatory":
            return f"{self.type}() -> {self.desc} "
        return f"{self.type}({self.v}) -> {self.desc} "

class OWBase(widget.OWWidget):
    name = "Generic widget"
    description = "description"

class Generic:
    def __init__(self, v=None):
        self.v = v


def get_variables(f):
    data = inspect.getfullargspec(f)
    args = data.args
    defs = data.defaults
    ann = data.annotations

    if defs:
        variables = {k: Variable("Obrigatory") for k in args[:-len(defs)]}
        for k, v in zip(args[-len(defs):], defs):
            variables[k] = Variable("Default", v)
    else:
        variables = {k: Variable("Obrigatory") for k in args}

    if ann:
        for v in ann:
            if not v in variables:
                continue
            variables[v].desc = ann[v]

    return variables


def create_widget(f):
    inputs= get_variables(f)
    #new_widget = type(f"Ow{f.__name__}", (widget.OWWidget,), {})
    new_widget = type(f"OW{f.__name__}",
                      OWBase.__bases__, dict(OWBase.__dict__))
    new_widget.name = f.__name__
    new_widget.description = f.__doc__

    new_widget.w = {}
    setattr(new_widget, "Inputs", type(f"OW{f.__name__}.Inputs", (), {}))
    setattr(new_widget, "Outputs", type(f"OW{f.__name__}.Outputs", (), {}))
    setattr(new_widget.Outputs, "out", Output("out", Generic))
    for k in inputs:
        setattr(new_widget.Inputs, k, Input(k, Generic, multiple=False))
        if inputs[k].type == "Default":
            new_widget.w[k] = Generic(inputs[k].v)

        def factory(name):
            def method(self, data, tid=None):
                self.w[name[4:]] = data
                #print("AQUI", name, data)
                try:
                    waux = {k: v.v for k, v in self.w.items()}
                    res = f(**waux)
                    self.Outputs.out.send(Generic(res))
                except Exception as e:
                    print(e)
            method.__name__ = name
            return method
        globals()[f"set_{k}"] = factory(f"set_{k}")
        setattr(new_widget, f"set_{k}", globals()[f"set_{k}"])
        getattr(new_widget.Inputs, k)(globals()[f"set_{k}"])

    globals()[f"OW{f.__name__}"] = new_widget

class OWGenericOut(widget.OWWidget):
    name = "Generic output"
    description = "description 123"

    class Outputs:
        out = Output("out", Generic)

    def __init__(self):
        self.Outputs.out.send(Generic(1))

#del OWBase


def three_var_func(a,b,c=5):
    """
     my cool doc
    """
    return a+b+c

def two_var_func(d,e=3):
    """
     my cool doc
    """
    return d+e

create_widget(three_var_func)
create_widget(two_var_func)

import pandas as pd

for attr in dir(pd):
    f= getattr(pd, attr)
    if callable(f) and hasattr(f, "__code__"):
        create_widget(f)