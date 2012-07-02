# vi: sw=4 ts=4 et:
"""scoring.py - cMonkey scoring base classes

This file is part of cMonkey Python. Please see README and LICENSE for
more information and licensing details.
"""
LOG_FORMAT = '%(asctime)s %(levelname)-8s %(message)s'

import logging
import os
import datamatrix as dm
from datetime import date
import util
import membership as memb
import numpy as np

# Official keys to access values in the configuration map
KEY_ORGANISM_CODE = 'organism_code'
KEY_NUM_ITERATIONS = 'num_iterations'
KEY_MATRIX_FILENAMES = 'matrix_filenames'
KEY_CACHE_DIR = 'cache_dir'
KEY_SEQUENCE_TYPES = 'sequence_types'
KEY_SEARCH_DISTANCES = 'search_distances'
KEY_SCAN_DISTANCES = 'scan_distances'
KEY_MULTIPROCESSING = 'multiprocessing'
KEY_OUTPUT_DIR = 'output_dir'
KEY_STRING_FILE = 'string_file'

USE_MULTIPROCESSING = True

def get_default_motif_scaling(num_iterations):
    """this scaling function is based on the tricky default motif scaling
    sequence in the R reference"""
    def default_motif_scaling(iteration):
        steps = int(round(num_iterations * 0.75))
        if iteration > steps:
            return 1.0
        else:
            return (1.0 / (steps - 1)) * (iteration - 1)
    return default_motif_scaling


def get_default_network_scaling(num_iterations):
    """this scaling function is based on the tricky default network scaling
    sequence in the R reference"""
    def default_network_scaling(iteration):
        steps = int(round(num_iterations * 0.75))
        if iteration > steps:
            return 0.5
        else:
            return (0.5 / (steps - 1)) * (iteration - 1)
    return default_network_scaling


def schedule(starts_at, every):
    def runs_in_iteration(iteration):
        return iteration >= starts_at and (iteration - starts_at) % every == 0
    return runs_in_iteration


def default_motif_iterations(iteration):
    return schedule(601, 3)


def default_meme_iterations(iteration):
    return schedule(600, 100)


def default_network_iterations(iteration):
    return schedule(1, 7)


class RunLog:
    """This is a class that captures information about a particular
    scoring function's behavior in a given iteration. In each iteration,
    a scoring function should log whether it was active and which scaling
    was applied.
    """
    def __init__(self, name):
        self.name = name
        self.active = []
        self.scaling = []

    def log(self, was_active, scaling):
        self.active.append(was_active)
        self.scaling.append(scaling)

    def __repr__(self):
        return "RunLog(" + self.name + ", " + str(self.active) + ", " + str(self.scaling) + ")"

    def to_json(self):
        return {'name': self.name, 'active': self.active, 'scaling': self.scaling}


class ScoringFunctionBase:
    """Base class for scoring functions"""

    def __init__(self, membership, matrix, scaling_func,
                 run_in_iteration=lambda iteration: True,
                 config_params={}):
        """creates a function instance"""
        self.__membership = membership
        self.__matrix = matrix
        self.__scaling_func = scaling_func
        self.run_in_iteration = run_in_iteration
        self.config_params = config_params
        if config_params == None:
            raise Exception('NO CONFIG PARAMS !!!')

        # returned in the iterations when the scoring algorithm is not run
        self.last_computed_result = None

    def name(self):
        """returns the name of this function"""
        raise Exception("please implement me")

    def membership(self):
        """returns this function's membership object"""
        return self.__membership

    def matrix(self):
        """returns this function's matrix object"""
        return self.__matrix

    def compute(self, iteration_result, reference_matrix=None):
        """general compute method,
        iteration_result is a dictionary that contains the
        results generated by the scoring functions in the
        current computation.
        the reference_matrix is actually a hack that allows the scoring
        function to normalize its scores to the range of a reference
        score matrix. In the normal case, those would be the gene expression
        row scores"""
        iteration = iteration_result['iteration']
        if self.run_in_iteration(iteration):
            self.last_computed_result = self.do_compute(iteration_result,
                                                        reference_matrix)

        self.run_log.log(self.run_in_iteration(iteration),
                         self.scaling(iteration_result['iteration']))
        return self.last_computed_result

    def compute_force(self, iteration_result, reference_matrix=None):
        """enforce computation, regardless of the iteration function"""
        iteration = iteration_result['iteration']
        self.last_computed_result = self.do_compute(iteration_result,
                                                    reference_matrix)

        self.run_log.log(self.run_in_iteration(iteration),
                         self.scaling(iteration_result['iteration']))
        return self.last_computed_result        

    def do_compute(self, iteration_result, ref_matrix=None):
        raise Execption("implement me")

    def num_clusters(self):
        """returns the number of clusters"""
        return self.__membership.num_clusters()

    def gene_names(self):
        """returns the gene names"""
        return self.__matrix.row_names

    def num_genes(self):
        """returns the number of rows"""
        return self.__matrix.num_rows()

    def gene_at(self, index):
        """returns the gene at the specified index"""
        return self.__matrix.row_name(index)

    def rows_for_cluster(self, cluster):
        """returns the rows for the specified cluster"""
        return self.__membership.rows_for_cluster(cluster)

    def scaling(self, iteration):
        """returns the quantile normalization scaling for the specified iteration"""
        if self.__scaling_func != None:
            return self.__scaling_func(iteration)
        else:
            return 0.0

    def store_checkpoint_data(self, shelf):
        """Default implementation does not store checkpoint data"""
        pass

    def restore_checkpoint_data(self, shelf):
        """Default implementation does not store checkpoint data"""
        pass

    def run_logs(self):
        """returns a list of RunLog objects, giving information about
        the last run of this function"""
        return []

class ColumnScoringFunction(ScoringFunctionBase):
    """Scoring algorithm for microarray data based on conditions.
    Note that the score does not correspond to the normal scoring
    function output format and can therefore not be combined in
    a generic way (the format is |condition x cluster|)"""

    def __init__(self, membership, matrix,
                 run_in_iteration=schedule(1, 5),
                 config_params=None):
        """create scoring function instance"""
        ScoringFunctionBase.__init__(self, membership,
                                     matrix, scaling_func=None,
                                     run_in_iteration=run_in_iteration,
                                     config_params=config_params)
        self.__last_computed_result = None
        self.run_log = RunLog("column_scoring")

    def name(self):
        """returns the name of this scoring function"""
        return "Column"

    def do_compute(self, iteration_result, ref_matrix=None):
        """compute method, iteration is the 0-based iteration number"""
        return compute_column_scores(self.membership(), self.matrix(),
                                     self.num_clusters())

def compute_column_scores(membership, matrix, num_clusters):
    """Computes the column scores for the specified number of clusters"""

    def compute_substitution(cluster_column_scores):
        """calculate substitution value for missing column scores"""
        membership_values = []
        for cluster in xrange(1, num_clusters + 1):
            columns = membership.columns_for_cluster(cluster)
            column_scores = cluster_column_scores[cluster - 1]
            if column_scores != None:
                for row in xrange(column_scores.num_rows()):
                    for col in xrange(column_scores.num_columns()):
                        if column_scores.column_name(col) in columns:
                            membership_values.append(column_scores[row][col])
        return util.quantile(membership_values, 0.95)

    cluster_column_scores = []
    null_scores_found = False
    for cluster in xrange(1, num_clusters + 1):
        submatrix = matrix.submatrix_by_name(
            row_names=membership.rows_for_cluster(cluster))
        if submatrix.num_rows() > 1:
            cluster_column_scores.append(compute_column_scores_submatrix(
                    submatrix))
        else:
            cluster_column_scores.append(None)
            null_scores_found = True

    if null_scores_found:
        substitution = compute_substitution(cluster_column_scores)

    # Convert scores into a matrix that have the clusters as columns
    # and conditions in the rows
    result = dm.DataMatrix(matrix.num_columns(), num_clusters,
                           row_names=matrix.column_names)
    for cluster in xrange(num_clusters):
        column_scores = cluster_column_scores[cluster]
        for row_index in xrange(matrix.num_columns()):
            if column_scores == None:
                result[row_index][cluster] = substitution
            else:
                result[row_index][cluster] = column_scores[0][row_index]
    result.fix_extreme_values()
    return result

def compute_column_scores_submatrix(matrix):
    """For a given matrix, compute the column scores.
    This is used to compute the column scores of the sub matrices that
    were determined by the pre-seeding, so typically, matrix is a
    submatrix of the input matrix that contains only the rows that
    belong to a certain cluster.
    The result is a DataMatrix with one row containing all the
    column scores

    This function normalizes diff^2 by the mean expression level, similar
    to "Index of Dispersion", see
    http://en.wikipedia.org/wiki/Index_of_dispersion
    for details
    """
    colmeans = matrix.column_means()
    matrix_minus_colmeans_squared = np.square(matrix.values - colmeans)
    var_norm = np.abs(colmeans) + 0.01
    result = util.column_means(matrix_minus_colmeans_squared) / var_norm
    return dm.DataMatrix(1, matrix.num_columns(), ['Col. Scores'],
                         matrix.column_names, [result])


class ScoringFunctionCombiner:
    """Taking advantage of the composite pattern, this combiner function
    exposes the basic interface of a scoring function in order to
    allow for nested scoring functions as they are used in the motif
    scoring
    """
    def __init__(self, membership, scoring_functions, scaling_func=None,
                 config_params=None,
                 log_subresults=False):
        """creates a combiner instance"""
        self.__membership = membership
        self.__scoring_functions = scoring_functions
        self.__log_subresults = log_subresults
        self.__scaling_func = scaling_func
        self.__config_params = config_params

    def compute_force(self, iteration_result, ref_matrix=None):
        """compute scores for one iteration, recursive force"""
        result_matrices = []
        score_scalings = []
        reference_matrix = ref_matrix
        iteration = iteration_result['iteration']
        for scoring_function in self.__scoring_functions:
            if reference_matrix == None and len(result_matrices) > 0:
                reference_matrix = result_matrices[0]

            matrix = scoring_function.compute_force(iteration_result, reference_matrix)
            if matrix != None:
                result_matrices.append(matrix)
                score_scalings.append(scoring_function.scaling(iteration))

                if self.__log_subresults:
                    self.__log_subresult(scoring_function, matrix)
        return self.__combine(result_matrices, score_scalings, iteration)

    def compute(self, iteration_result, ref_matrix=None):
        """compute scores for one iteration"""
        result_matrices = []
        score_scalings = []
        reference_matrix = ref_matrix
        iteration = iteration_result['iteration']
        for scoring_function in self.__scoring_functions:
            # This  is actually a hack in order to propagate
            # a reference matrix to the compute function
            # This could have negative impact on scalability
            if reference_matrix == None and len(result_matrices) > 0:
                reference_matrix = result_matrices[0]

            matrix = scoring_function.compute(iteration_result, reference_matrix)
            if matrix != None:
                result_matrices.append(matrix)
                score_scalings.append(scoring_function.scaling(iteration))

                if self.__log_subresults:
                    self.__log_subresult(scoring_function, matrix)

        return self.__combine(result_matrices, score_scalings, iteration)

    def __combine(self, result_matrices, score_scalings, iteration):
        if len(result_matrices) > 1 and self.__config_params['quantile_normalize']:
            start_time = util.current_millis()
            result_matrices = dm.quantile_normalize_scores(result_matrices,
                                                           score_scalings)
            elapsed = util.current_millis() - start_time
            logging.info("quantile normalize in %f s.", elapsed / 1000.0)

        if len(result_matrices) > 0:
            start_time = util.current_millis()
            combined_score = (result_matrices[0] *
                              self.__scoring_functions[0].scaling(iteration))
            for index in xrange(1, len(result_matrices)):
                combined_score += (
                    result_matrices[index] *
                    self.__scoring_functions[index].scaling(iteration))
            elapsed = util.current_millis() - start_time
            logging.info("combined score in %f s.", elapsed / 1000.0)
            return combined_score
        else:
            return None

    def __log_subresult(self, score_function, matrix):
        """output an accumulated subresult to the log"""
        scores = []
        for cluster in xrange(1, matrix.num_columns() + 1):
            cluster_rows = self.__membership.rows_for_cluster(cluster)
            for row in xrange(matrix.num_rows()):
                if matrix.row_name(row) in cluster_rows:
                    scores.append(matrix[row][cluster - 1])
        logging.info("function '%s', trim mean score: %f",
                     score_function.name(),
                     util.trim_mean(scores, 0.05))

    def scaling(self, iteration):
        """returns the scaling for the specified iteration"""
        return self.__scaling_func(iteration)

    def store_checkpoint_data(self, shelf):
        """recursively invokes store_checkpoint_data() on the children"""
        for scoring_func in self.__scoring_functions:
            scoring_func.store_checkpoint_data(shelf)

    def restore_checkpoint_data(self, shelf):
        """recursively invokes store_checkpoint_data() on the children"""
        for scoring_func in self.__scoring_functions:
            scoring_func.restore_checkpoint_data(shelf)

    def run_logs(self):
        """joins all contained function's run logs"""
        result = []
        for scoring_func in self.__scoring_functions:
            result.extend(scoring_func.run_logs())
        return result
