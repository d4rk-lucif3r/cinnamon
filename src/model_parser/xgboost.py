import numpy as np
import pandas as pd
from typing import Tuple
from .single_tree import BinaryTree
import xgboost
from .i_tree_ensemble import ITreeEnsembleParser
import struct
from packaging import version
from scipy.special import logit


class BinaryParser:
    def __init__(self, buf):
        self.buf = buf
        self.pos = 0

    def read(self, dtype):
        size = struct.calcsize(dtype)
        val = struct.unpack(dtype, self.buf[self.pos:self.pos+size])[0]
        self.pos += size
        return val

    def read_arr(self, dtype, n_items):
        format = "%d%s" % (n_items, dtype)
        size = struct.calcsize(format)
        val = struct.unpack(format, self.buf[self.pos:self.pos+size])[0]
        self.pos += size
        return val

    def read_str(self, size):
        val = self.buf[self.pos:self.pos+size].decode('utf-8')
        self.pos += size
        return val


class XGBoostParser(ITreeEnsembleParser):
    objective_task_map = {'reg:squarederror': 'regression',
                          'reg:squaredlogerror': 'regression',
                          'reg:logistic': 'regression',
                          'reg:pseudohubererror': 'regression',
                          'binary:logistic': 'classification',
                          'binary:logitraw': 'classification',
                          'binary:hinge': 'classification',
                          'count:poisson': 'regression',
                          'survival:cox': 'regression',
                          'survival:aft': 'regression',
                          'aft_loss_distribution': 'regression',
                          'multi:softmax': 'classification',
                          'multi:softprob': 'classification',
                          'rank:pairwise': 'ranking',
                          'rank:ndcg': 'ranking',
                          'rank:map': 'ranking',
                          'reg:gamma': 'regression',
                          'reg:tweedie': 'regression',
                          }

    def __init__(self, model_type: str):
        super().__init__()
        self.model_type = model_type

    def parse(self, model: xgboost.core.Booster, iteration_range: Tuple[int,int]):
        self.original_model = model
        parsed_info = self._parse_binary(self.original_model.save_raw().lstrip(b'binf'))
        if parsed_info['booster_type'] != 'gbtree':
            raise ValueError(f"{parsed_info['booster_type']} booster for XGBoost model is not supported")
        self.max_depth = parsed_info['max_depth']
        self.n_features = parsed_info['n_features']
        self.model_objective = parsed_info['model_objective']
        self.prediction_dim = 1 if parsed_info['n_class'] == 0 else parsed_info['n_class']
        if self.model_objective in self.objective_task_map:
            self.task = self.objective_task_map[self.model_objective]
        else:
            raise ValueError(f"{self.model_objective} objective for XGBoost model is not supported")
        self.iteration_range = self._get_iteration_range(iteration_range,
                                                         parsed_info['num_trees'] // self.prediction_dim)
        self.n_trees = self.iteration_range[1] - self.iteration_range[0]
        self.trees = self._get_trees(parsed_info, self.iteration_range, self.n_trees)

    @staticmethod
    def _parse_binary(buf):
        parsed_info = {}
        binary_parser = BinaryParser(buf)

        ###################
        # load the model parameters
        parsed_info['base_score'] = binary_parser.read('f')  # used ?
        parsed_info['n_features'] = binary_parser.read('I')
        parsed_info['n_class'] = binary_parser.read('i')
        parsed_info['contain_extra_attrs'] = binary_parser.read('i')  # not used
        parsed_info['contain_eval_metrics'] = binary_parser.read('i')  # not used
        binary_parser.read_arr('i', 29)  # reserved
        parsed_info['model_objective_len'] = binary_parser.read('Q')  # used only at next line
        parsed_info['model_objective'] = binary_parser.read_str(parsed_info['model_objective_len'])
        parsed_info['booster_type_len'] = binary_parser.read('Q')  # not used
        parsed_info['booster_type'] = binary_parser.read_str(parsed_info['booster_type_len'])  # not used

        # new in XGBoost 1.0 is that the base_score is saved untransformed (https://github.com/dmlc/xgboost/pull/5101)
        # so we have to transform it depending on the objective
        if version.parse(xgboost.__version__).major >= 1:
            if parsed_info['model_objective'] in ["binary:logistic", "reg:logistic"]:
                parsed_info['base_score'] = logit(parsed_info['base_score'])  # pylint: disable=no-member

        assert parsed_info['booster_type'] == "gbtree", "Only the 'gbtree' model type is supported, not '%s'!" % parsed_info['booster_type']

        # load the gbtree specific parameters
        parsed_info['num_trees'] = binary_parser.read('i')
        parsed_info['num_roots'] = binary_parser.read('i')  # not used
        parsed_info['num_feature_gbtree'] = binary_parser.read('i')  # not used
        parsed_info['pad_32bit'] = binary_parser.read('i')  # not used
        parsed_info['num_pbuffer_deprecated'] = binary_parser.read('Q')  # not used
        parsed_info['num_output_group'] = binary_parser.read('i')  # not used
        parsed_info['size_leaf_vector'] = binary_parser.read('i')  # not used
        binary_parser.read_arr('i', 32)  # reserved

        # load each tree
        parsed_info['num_roots'] = np.zeros(parsed_info['num_trees'], dtype=np.int32)  # not used
        parsed_info['num_nodes'] = np.zeros(parsed_info['num_trees'], dtype=np.int32)
        parsed_info['num_deleted'] = np.zeros(parsed_info['num_trees'], dtype=np.int32)
        parsed_info['max_depth'] = np.zeros(parsed_info['num_trees'], dtype=np.int32)
        parsed_info['num_features_tree'] = np.zeros(parsed_info['num_trees'], dtype=np.int32)
        parsed_info['size_leaf_vector'] = np.zeros(parsed_info['num_trees'], dtype=np.int32)
        parsed_info['node_parents'] = []
        parsed_info['node_cleft'] = []
        parsed_info['node_cright'] = []
        parsed_info['node_sindex'] = []
        parsed_info['node_info'] = []
        parsed_info['loss_chg'] = []
        parsed_info['sum_hess'] = []
        parsed_info['base_weight'] = []
        parsed_info['leaf_child_cnt'] = []

        for i in range(parsed_info['num_trees']):
            # load the per-tree params
            parsed_info['num_roots'][i] = binary_parser.read('i')
            parsed_info['num_nodes'][i] = binary_parser.read('i')
            parsed_info['num_deleted'][i] = binary_parser.read('i')
            parsed_info['max_depth'][i] = binary_parser.read('i')
            parsed_info['num_features_tree'][i] = binary_parser.read('i')
            parsed_info['size_leaf_vector'][i] = binary_parser.read('i')

            # load the nodes
            binary_parser.read_arr('i', 31)  # reserved
            parsed_info['node_parents'].append(np.zeros(parsed_info['num_nodes'][i], dtype=np.int32))
            parsed_info['node_cleft'].append(np.zeros(parsed_info['num_nodes'][i], dtype=np.int32))
            parsed_info['node_cright'].append(np.zeros(parsed_info['num_nodes'][i], dtype=np.int32))
            parsed_info['node_sindex'].append(np.zeros(parsed_info['num_nodes'][i], dtype=np.uint32))
            parsed_info['node_info'].append(np.zeros(parsed_info['num_nodes'][i], dtype=np.float32))
            for j in range(parsed_info['num_nodes'][i]):
                parsed_info['node_parents'][-1][j] = binary_parser.read('i')
                parsed_info['node_cleft'][-1][j] = binary_parser.read('i')
                parsed_info['node_cright'][-1][j] = binary_parser.read('i')
                parsed_info['node_sindex'][-1][j] = binary_parser.read('I')
                parsed_info['node_info'][-1][j] = binary_parser.read('f')

            # load the stat nodes
            parsed_info['loss_chg'].append(np.zeros(parsed_info['num_nodes'][i], dtype=np.float32))
            parsed_info['sum_hess'].append(np.zeros(parsed_info['num_nodes'][i], dtype=np.float32))
            parsed_info['base_weight'].append(np.zeros(parsed_info['num_nodes'][i], dtype=np.float32))
            parsed_info['leaf_child_cnt'].append(np.zeros(parsed_info['num_nodes'][i], dtype=np.int32))
            for j in range(parsed_info['num_nodes'][i]):
                parsed_info['loss_chg'][-1][j] = binary_parser.read('f')
                parsed_info['sum_hess'][-1][j] = binary_parser.read('f')
                parsed_info['base_weight'][-1][j] = binary_parser.read('f')
                parsed_info['leaf_child_cnt'][-1][j] = binary_parser.read('i')

        return parsed_info

    @staticmethod
    def _get_trees(parsed_info, iteration_range, n_trees):

        shape = (n_trees, parsed_info['num_nodes'][iteration_range[0]:iteration_range[1]].max())
        children_default = np.zeros(shape, dtype=np.int32)
        split_features_index = np.zeros(shape, dtype=np.int32)
        split_values = np.zeros(shape, dtype=np.float32)
        values = np.zeros((shape[0], shape[1], 1), dtype=np.float32)
        trees = []
        for i in range(iteration_range[0], iteration_range[1]):
            for j in range(parsed_info['num_nodes'][i]):
                if np.right_shift(parsed_info['node_sindex'][i][j], np.uint32(31)) != 0:
                    children_default[i,j] = parsed_info['node_cleft'][i][j]
                else:
                    children_default[i,j] = parsed_info['node_cright'][i][j]
                split_features_index[i,j] = parsed_info['node_sindex'][i][j] & ((np.uint32(1) << np.uint32(31)) -
                                                                                np.uint32(1))
                if parsed_info['node_cleft'][i][j] >= 0:
                    # Xgboost uses < for split_values where shap uses <=
                    # Move the threshold down by the smallest possible increment
                    split_values[i, j] = np.nextafter(parsed_info['node_info'][i][j], - np.float32(np.inf))
                else:
                    values[i,j] = parsed_info['node_info'][i][j]

            l = len(parsed_info['node_cleft'][i])
            trees.append(BinaryTree(children_left=parsed_info['node_cleft'][i],
                                    children_right=parsed_info['node_cright'][i],
                                    children_default=children_default[i,:l],
                                    split_features_index=split_features_index[i,:l],
                                    split_values=split_values[i,:l],
                                    values=values[i,:l],
                                    train_node_weights=parsed_info['sum_hess'][i],
                                    n_features=parsed_info['n_features']))
        return trees

    @staticmethod
    def print_info(parsed_info):

        print("--- global parmeters ---")
        print("base_score =", parsed_info['base_score'])
        print("n_features =", parsed_info['n_features'])
        print("n_class =", parsed_info['n_class'])
        print("contain_extra_attrs =", parsed_info['contain_extra_attrs'])
        print("contain_eval_metrics =", parsed_info['contain_eval_metrics'])
        print("model_objective_len =", parsed_info['model_objective_len'])
        print("model_objective =", parsed_info['model_objective'])
        print("booster_type_len =", parsed_info['booster_type_len'])
        print("booster_type =", parsed_info['booster_type'])
        print()
        print("--- gbtree specific parameters ---")
        print("num_trees =", parsed_info['num_trees'])
        print("num_features_tree =", parsed_info['num_features_tree'])
        print("num_roots =", parsed_info['num_roots'])
        print("pad_32bit =", parsed_info['pad_32bit'])
        print("num_pbuffer_deprecated =", parsed_info['num_pbuffer_deprecated'])
        print("num_output_group =", parsed_info['num_output_group'])
        print("size_leaf_vector =", parsed_info['size_leaf_vector'])

    def predict_leaf(self, X: pd.DataFrame):
        return self.original_model.predict(xgboost.DMatrix(X), pred_leaf=True, iteration_range=self.iteration_range)

    def predict_raw(self, X: pd.DataFrame):
        return self.original_model.predict(xgboost.DMatrix(X), output_margin=True,
                                           iteration_range=self.iteration_range)

    def predict_proba(self, X: pd.DataFrame):
        return self.original_model.predict(xgboost.DMatrix(X), iteration_range=self.iteration_range)
