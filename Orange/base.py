import inspect
import itertools
from collections import Iterable
import re

import numpy as np
import scipy

from Orange.data import Table, Storage, Instance, Value
from Orange.data.filter import HasClass
from Orange.data.table import DomainTransformationError
from Orange.data.util import one_hot
from Orange.misc.wrapper_meta import WrapperMeta
from Orange.preprocess import Continuize, RemoveNaNColumns, SklImpute, Normalize
from Orange.statistics.util import all_nan
from Orange.util import Reprable

__all__ = ["Learner", "Model", "SklLearner", "SklModel",
           "ReprableWithPreprocessors"]


class ReprableWithPreprocessors(Reprable):
    def _reprable_omit_param(self, name, default, value):
        if name == "preprocessors":
            default_cls = type(self).preprocessors
            if value is default or value is default_cls:
                return True
            else:
                try:
                    return all(p1 is p2 for p1, p2 in
                               itertools.zip_longest(value, default_cls))
                except (ValueError, TypeError):
                    return False
        else:
            return super()._reprable_omit_param(name, default, value)


class Learner(ReprableWithPreprocessors):
    """The base learner class.

    Preprocessors can behave in a number of different ways, all of which are
    described here.
    If the user does not pass a preprocessor argument into the Learner
    constructor, the default learner preprocessors are used. We assume the user
    would simply like to get things done without having to worry about
    preprocessors.
    If the user chooses to pass in their own preprocessors, we assume they know
    what they are doing. In this case, only the user preprocessors are used and
    the default preprocessors are ignored.
    In case the user would like to use the default preprocessors as well as
    their own ones, the `use_default_preprocessors` flag should be set.

    Parameters
    ----------
    preprocessors : Preprocessor or tuple[Preprocessor], optional
        User defined preprocessors. If the user specifies their own
        preprocessors, the default ones will not be used, unless the
        `use_default_preprocessors` flag is set.

    Attributes
    ----------
    preprocessors : tuple[Preprocessor] (default None)
        The used defined preprocessors that will be used on any data.
    use_default_preprocessors : bool (default False)
        This flag indicates whether to use the default preprocessors that are
        defined on the Learner class. Since preprocessors can be applied in a
        number of ways
    active_preprocessors : tuple[Preprocessor]
        The processors that will be used when data is passed to the learner.
        This depends on whether the user has passed in their own preprocessors
        and whether the `use_default_preprocessors` flag is set.

        This property is needed mainly because of the `Fitter` class, which can
        not know in advance, which preprocessors it will need to use. Therefore
        this resolves the active preprocessors using a lazy approach.
    params : dict
        The params that the learner is constructed with.

    """
    supports_multiclass = False
    supports_weights = False
    #: A sequence of data preprocessors to apply on data prior to
    #: fitting the model
    preprocessors = ()
    learner_adequacy_err_msg = ''

    def __init__(self, preprocessors=None):
        self.use_default_preprocessors = False
        if isinstance(preprocessors, Iterable):
            self.preprocessors = tuple(preprocessors)
        elif preprocessors:
            self.preprocessors = (preprocessors,)

    def fit(self, X, Y, W=None):
        raise RuntimeError(
            "Descendants of Learner must overload method fit or fit_storage")

    def fit_storage(self, data):
        """Default implementation of fit_storage defaults to calling fit.
        Derived classes must define fit_storage or fit"""
        X, Y, W = data.X, data.Y, data.W if data.has_weights() else None
        return self.fit(X, Y, W)

    def __call__(self, data):
        if not self.check_learner_adequacy(data.domain):
            raise ValueError(self.learner_adequacy_err_msg)

        origdomain = data.domain

        if isinstance(data, Instance):
            data = Table(data.domain, [data])
        origdata = data
        data = self.preprocess(data)

        if len(data.domain.class_vars) > 1 and not self.supports_multiclass:
            raise TypeError("%s doesn't support multiple class variables" %
                            self.__class__.__name__)

        model = self._fit_model(data)
        model.used_vals = [np.unique(y).astype(int) for y in data.Y[:, None].T]
        model.domain = data.domain
        model.supports_multiclass = self.supports_multiclass
        model.name = self.name
        model.original_domain = origdomain
        model.original_data = origdata
        return model

    def _fit_model(self, data):
        if type(self).fit is Learner.fit:
            return self.fit_storage(data)
        else:
            X, Y, W = data.X, data.Y, data.W if data.has_weights() else None
            return self.fit(X, Y, W)

    def preprocess(self, data):
        """Apply the `preprocessors` to the data"""
        for pp in self.active_preprocessors:
            data = pp(data)
        return data

    @property
    def active_preprocessors(self):
        yield from self.preprocessors
        if (self.use_default_preprocessors and
                self.preprocessors is not type(self).preprocessors):
            yield from type(self).preprocessors

    def check_learner_adequacy(self, domain):
        return True

    @property
    def name(self):
        """Return a short name derived from Learner type name"""
        try:
            return self.__name
        except AttributeError:
            name = self.__class__.__name__
            if name.endswith('Learner'):
                name = name[:-len('Learner')]
            if name.endswith('Fitter'):
                name = name[:-len('Fitter')]
            if isinstance(self, SklLearner) and name.startswith('Skl'):
                name = name[len('Skl'):]
            name = name or 'learner'
            # From http://stackoverflow.com/a/1176023/1090455 <3
            self.name = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2',
                               re.sub(r'(.)([A-Z][a-z]+)', r'\1 \2', name)).lower()
            return self.name

    @name.setter
    def name(self, value):
        self.__name = value

    def __str__(self):
        return self.name


class Model(Reprable):
    supports_multiclass = False
    supports_weights = False
    Value = 0
    Probs = 1
    ValueProbs = 2

    def __init__(self, domain=None, original_domain=None):
        if isinstance(self, Learner):
            domain = None
        elif domain is None:
            raise ValueError("unspecified domain")
        self.domain = domain
        if original_domain is not None:
            self.original_domain = original_domain
        else:
            self.original_domain = domain

    def predict(self, X):
        if type(self).predict_storage is Model.predict_storage:
            raise TypeError("Descendants of Model must overload method predict")
        else:
            Y = np.zeros((len(X), len(self.domain.class_vars)))
            Y[:] = np.nan
            table = Table(self.domain, X, Y)
            return self.predict_storage(table)

    def predict_storage(self, data):
        if isinstance(data, Storage):
            return self.predict(data.X)
        elif isinstance(data, Instance):
            return self.predict(np.atleast_2d(data.x))
        raise TypeError("Unrecognized argument (instance of '{}')"
                        .format(type(data).__name__))

    def get_backmappers(self, data):
        backmappers = []
        n_values = []

        dataclasses = data.domain.class_vars
        modelclasses = self.domain.class_vars
        if not (modelclasses and dataclasses):
            return None, []  # classless model or data; don't touch
        if len(dataclasses) != len(modelclasses):
            raise DomainTransformationError(
                "Mismatching number of model's classes and data classes")
        for dataclass, modelclass in zip(dataclasses, modelclasses):
            if dataclass != modelclass:
                if dataclass.name != modelclass.name:
                    raise DomainTransformationError(
                        f"Model for '{modelclass.name}' "
                        f"cannot predict '{dataclass.name}'")
                else:
                    raise DomainTransformationError(
                        f"Variables '{modelclass.name}' in the model is "
                        "incompatible with the variable of the same name "
                        "in the data.")
            n_values.append(dataclass.is_discrete and len(dataclass.values))
            if dataclass is not modelclass and dataclass.is_discrete:
                backmappers.append(dataclass.get_mapper_from(modelclass))
            else:
                backmappers.append(None)
        if all(x is None for x in backmappers):
            backmappers = None
        return backmappers, n_values

    def backmap_value(self, value, mapped_probs, n_values, backmappers):
        if backmappers is None:
            return value

        if value.ndim == 2:  # For multitarget, recursive call by columns
            new_value = np.zeros(value.shape)
            for i, n_value, backmapper in zip(
                    itertools.count(), n_values, backmappers):
                new_value[:, i] = self.backmap_value(
                    value[:, i], mapped_probs[:, i, :], [n_value], [backmapper])
            return new_value

        backmapper = backmappers[0]
        if backmapper is None:
            return value

        value = backmapper(value)
        nans = np.isnan(value)
        if not np.any(nans) or n_values[0] < 2:
            return value
        if mapped_probs is not None:
            value[nans] = np.argmax(mapped_probs[nans], axis=1)
        else:
            value[nans] = np.random.RandomState(0).choice(
                backmapper(np.arange(0, n_values[0] - 1)),
                (np.sum(nans), ))
        return value

    def backmap_probs(self, probs, n_values, backmappers):
        if backmappers is None:
            return probs

        if probs.ndim == 3:
            new_probs = np.zeros((len(probs), len(n_values), max(n_values)),
                                 dtype=probs.dtype)
            for i, n_value, backmapper in zip(
                    itertools.count(), n_values, backmappers):
                new_probs[:, i, :n_value] = self.backmap_probs(
                    probs[:, i, :], [n_value], [backmapper])
            return new_probs

        backmapper = backmappers[0]
        if backmapper is None:
            return probs
        n_value = n_values[0]
        new_probs = np.zeros((len(probs), n_value), dtype=probs.dtype)
        for col in range(probs.shape[1]):
            target = backmapper(col)
            if not np.isnan(target):
                new_probs[:, int(target)] = probs[:, col]
        tots = np.sum(new_probs, axis=1)
        zero_sum = tots == 0
        new_probs[zero_sum] = 1
        tots[zero_sum] = n_value
        new_probs = new_probs / tots[:, None]
        return new_probs

    def __call__(self, data, ret=Value):
        multitarget = len(self.domain.class_vars) > 1

        def one_hot_probs(value):
            if not multitarget:
                return one_hot(value)

            max_card = max(len(c.values) for c in self.domain.class_vars)
            probs = np.zeros(value.shape + (max_card,), float)
            for i in range(len(self.domain.class_vars)):
                probs[:, i, :] = one_hot(value[:, i])
            return probs

        def fix_dim(x):
            return x[0] if one_d else x

        def data_to_model_domain():
            if data.domain == self.domain:
                return data

            if self.original_domain.attributes != data.domain.attributes \
                    and data.X.size \
                    and not all_nan(data.X):
                new_data = data.transform(self.original_domain)
                if all_nan(new_data.X):
                    raise DomainTransformationError(
                        "domain transformation produced no defined values")
                return new_data.transform(self.domain)

            return data.transform(self.domain)

        if not 0 <= ret <= 2:
            raise ValueError("invalid value of argument 'ret'")
        if ret > 0 and any(v.is_continuous for v in self.domain.class_vars):
            raise ValueError("cannot predict continuous distributions")

        # Convert 1d structures to 2d and remember doing it
        one_d = True
        if isinstance(data, Instance):
            data = Table.from_list(data.domain, [data])
        elif isinstance(data, (list, tuple)) \
                and not isinstance(data[0], (list, tuple)):
            data = [data]
        elif isinstance(data, np.ndarray) and data.ndim == 1:
            data = np.atleast_2d(data)
        else:
            one_d = False

        # Call the predictor
        backmappers = None
        n_values = []
        if isinstance(data, (np.ndarray, scipy.sparse.csr.csr_matrix)):
            prediction = self.predict(data)
        elif isinstance(data, Table):
            backmappers, n_values = self.get_backmappers(data)
            data = data_to_model_domain()
            prediction = self.predict_storage(data)
        elif isinstance(data, (list, tuple)):
            data = Table.from_list(self.original_domain, data)
            data = data.transform(self.domain)
            prediction = self.predict_storage(data)
        else:
            raise TypeError("Unrecognized argument (instance of '{}')"
                            .format(type(data).__name__))

        # Parse the result into value and probs
        if isinstance(prediction, tuple):
            value, probs = prediction
        elif prediction.ndim == 1 + multitarget:
            value, probs = prediction, None
        elif prediction.ndim == 2 + multitarget:
            value, probs = None, prediction
        else:
            raise TypeError("model returned a %i-dimensional array",
                            prediction.ndim)

        # Ensure that we have what we need to return; backmapp everything
        if probs is None and (ret != Model.Value or backmappers is not None):
            probs = one_hot_probs(value)
        if probs is not None:
            probs = self.backmap_probs(probs, n_values, backmappers)
        if ret != Model.Probs:
            if value is None:
                value = np.argmax(probs, axis=-1)
                # probs are already backmapped
            else:
                value = self.backmap_value(value, probs, n_values, backmappers)

        # Return what we need to
        if ret == Model.Probs:
            return fix_dim(probs)
        if isinstance(data, Instance) and not multitarget:
            value = [Value(self.domain.class_var, value[0])]
        if ret == Model.Value:
            return fix_dim(value)
        else:  # ret == Model.ValueProbs
            return fix_dim(value), fix_dim(probs)

    def __getstate__(self):
        """Skip (possibly large) data when pickling models"""
        state = self.__dict__
        if 'original_data' in state:
            state = state.copy()
            state['original_data'] = None
        return state


class SklModel(Model, metaclass=WrapperMeta):
    used_vals = None

    def __init__(self, skl_model):
        self.skl_model = skl_model

    def predict(self, X):
        value = self.skl_model.predict(X)
        # SVM has probability attribute which defines if method compute probs
        has_prob_attr = hasattr(self.skl_model, "probability")
        if (has_prob_attr and self.skl_model.probability
                or not has_prob_attr
                and hasattr(self.skl_model, "predict_proba")):
            probs = self.skl_model.predict_proba(X)
            return value, probs
        return value

    def __repr__(self):
        # Params represented as a comment because not passed into constructor
        return super().__repr__() + '  # params=' + repr(self.params)


class SklLearner(Learner, metaclass=WrapperMeta):
    """
    ${skldoc}
    Additional Orange parameters

    preprocessors : list, optional
        An ordered list of preprocessors applied to data before
        training or testing.
        Defaults to
        `[RemoveNaNClasses(), Continuize(), SklImpute(), RemoveNaNColumns()]`
    """
    __wraps__ = None
    __returns__ = SklModel
    _params = {}

    preprocessors = default_preprocessors = [
        HasClass(),
        Continuize(),
        RemoveNaNColumns(),
        SklImpute()]

    @property
    def params(self):
        return self._params

    @params.setter
    def params(self, value):
        self._params = self._get_sklparams(value)

    def _get_sklparams(self, values):
        skllearner = self.__wraps__
        if skllearner is not None:
            spec = inspect.getargs(skllearner.__init__.__code__)
            # first argument is 'self'
            assert spec.args[0] == "self"
            params = {name: values[name] for name in spec.args[1:]
                      if name in values}
        else:
            raise TypeError("Wrapper does not define '__wraps__'")
        return params

    def preprocess(self, data):
        data = super().preprocess(data)

        if any(v.is_discrete and len(v.values) > 2
               for v in data.domain.attributes):
            raise ValueError("Wrapped scikit-learn methods do not support " +
                             "multinomial variables.")

        return data

    def __call__(self, data):
        m = super().__call__(data)
        m.params = self.params
        return m

    def _initialize_wrapped(self):
        return self.__wraps__(**self.params)

    def fit(self, X, Y, W=None):
        clf = self._initialize_wrapped()
        Y = Y.reshape(-1)
        if W is None or not self.supports_weights:
            return self.__returns__(clf.fit(X, Y))
        return self.__returns__(clf.fit(X, Y, sample_weight=W.reshape(-1)))

    @property
    def supports_weights(self):
        """Indicates whether this learner supports weighted instances.
        """
        return 'sample_weight' in self.__wraps__.fit.__code__.co_varnames

    def __getattr__(self, item):
        try:
            return self.params[item]
        except (KeyError, AttributeError):
            raise AttributeError(item) from None

    # TODO: Disallow (or mirror) __setattr__ for keys in params?

    def __dir__(self):
        dd = super().__dir__()
        return list(sorted(set(dd) | set(self.params.keys())))


class TreeModel(Model):
    pass


class RandomForestModel(Model):
    """Interface for random forest models
    """

    @property
    def trees(self):
        """Return a list of Trees in the forest

        Returns
        -------
        List[Tree]
        """


class KNNBase:
    """Base class for KNN (classification and regression) learners
    """
    def __init__(self, n_neighbors=5, metric="euclidean", weights="uniform",
                 algorithm='auto', metric_params=None,
                 preprocessors=None):
        super().__init__(preprocessors=preprocessors)
        self.params = vars()

    def fit(self, X, Y, W=None):
        if self.params["metric_params"] is None and \
                        self.params.get("metric") == "mahalanobis":
            self.params["metric_params"] = {"V": np.cov(X.T)}
        return super().fit(X, Y, W)


class NNBase:
    """Base class for neural network (classification and regression) learners
    """
    preprocessors = SklLearner.preprocessors + [Normalize()]

    def __init__(self, hidden_layer_sizes=(100,), activation='relu',
                 solver='adam', alpha=0.0001, batch_size='auto',
                 learning_rate='constant', learning_rate_init=0.001,
                 power_t=0.5, max_iter=200, shuffle=True, random_state=None,
                 tol=0.0001, verbose=False, warm_start=False, momentum=0.9,
                 nesterovs_momentum=True, early_stopping=False,
                 validation_fraction=0.1, beta_1=0.9, beta_2=0.999,
                 epsilon=1e-08, preprocessors=None):
        super().__init__(preprocessors=preprocessors)
        self.params = vars()
