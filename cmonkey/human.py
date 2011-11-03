"""human.py - cMonkey human specific module

This file is part of cMonkey Python. Please see README and LICENSE for
more information and licensing details.
"""
import os
import logging
import numpy
import scipy
import util
import thesaurus
import organism
import datamatrix as dm
import membership as memb
import microarray
import stringdb
import network as nw
import motif
import meme

LOG_FORMAT = '%(asctime)s %(levelname)-8s %(message)s'
CACHE_DIR = 'humancache'
CONTROLS_FILE = 'human_data/controls.csv'
RUG_FILE = 'human_data/rug.csv'

PROM_SEQFILE = 'human_data/promoterSeqs_set3pUTR_Final.csv.gz'
P3UTR_SEQFILE = 'human_data/p3utrSeqs_set3pUTR_Final.csv.gz'
THESAURUS_FILE = 'human_data/synonymThesaurus.csv.gz'

RUG_PROPS = ['MIXED', 'ASTROCYTOMA', 'GBM', 'OLIGODENDROGLIOMA']
NUM_CLUSTERS = 133
ROW_WEIGHT = 6.0
NUM_ITERATIONS = 2000

SEARCH_DISTANCES = {'promoter': (0, 700), 'p3utr': (0, 831)}
SCAN_DISTANCES = {'promoter': (0, 700), 'p3utr': (0, 831)}


def genes_per_group_proportional(num_genes_total, num_per_group):
    """takes the total number of genes and a dictionary containing
    the number of members for each group and returns a dictionary that
    distributes the number of genes proportionally to each group"""
    result = {}
    num_group_elems = sum(num_per_group.values())
    groups = num_per_group.keys()
    for index in range(len(groups)):
        group = groups[index]
        if index == len(groups) - 1:
            result[group] = num_genes_total - sum(result.values())
        else:
            result[group] = int(float(num_genes_total) *
                                float(num_per_group[group]) /
                                float(num_group_elems))
    return result


def genes_per_group_nonproportional(num_genes_total, groups):
    """distributes the number of genes evenly to each group given"""
    result = {}
    partition = int(float(num_genes_total) / float(len(groups)))
    for index in range(len(groups)):
        if index == len(groups) - 1:
            result[groups[index]] = num_genes_total - sum(result.values())
        else:
            result[groups[index]] = partition
    return result


def select_probes(matrix, num_genes_total, column_groups, proportional=True):
    """select probes proportional, column_groups is a map from a group
    label to column indexes in the matrix"""
    def coeff_var(row_values):
        """computes the coefficient of variation"""
        sigma = util.r_stddev(row_values)
        mu = numpy.mean(row_values)
        return sigma / mu

    num_per_group = {group: len(indexes)
                     for group, indexes in column_groups.items()}
    if proportional:
        per_group = genes_per_group_proportional(num_genes_total,
                                                 num_per_group)
    else:
        per_group = genes_per_group_proportional(num_genes_total,
                                                 column_groups.keys())

    cvrows = []
    for group, col_indexes in column_groups.items():
        group_cvs = []
        for row in range(matrix.num_rows()):
            row_values = [matrix[row][col] for col in col_indexes]
            group_cvs.append(coeff_var(row_values))
        cvrows += [group_cvs.index(value)
                   for value in
                   sorted(group_cvs, reverse=True)][:per_group[group]]
    return sorted(list(set(cvrows)))


def intensities_to_ratios(matrix, controls, column_groups):
    """turn intensities into ratios
    Warning: input matrix is modified !!!"""
    control_indexes = [matrix.column_names().index(control)
                       for control in controls
                       if control in matrix.column_names()]
    for group_columns in column_groups.values():
        group_controls = [index for index in control_indexes
                          if index in group_columns]
        means = []
        for row in range(matrix.num_rows()):
            values = [float(matrix[row][col]) for col in group_controls]
            means.append(sum(values) / float(len(values)))

        for col in group_columns:
            for row in range(matrix.num_rows()):
                matrix[row][col] /= means[row]

        center_scale_filter(matrix, group_columns, group_controls)
    return matrix


def center_scale_filter(matrix, group_columns, group_controls):
    """center the values of each row around their median and scale
    by their standard deviation. This is a specialized version"""
    centers = [scipy.median([matrix[row][col]
                             for col in group_controls])
               for row in range(matrix.num_rows())]
    scale_factors = [util.r_stddev([matrix[row][col]
                                    for col in group_columns])
                     for row in range(matrix.num_rows())]
    for row in range(matrix.num_rows()):
        for col in group_columns:
            matrix[row][col] -= centers[row]
            matrix[row][col] /= scale_factors[row]
    return matrix


##################
# Organism interface

class Human(organism.OrganismBase):
    """Implementation of a human organism"""

    def __init__(self, prom_seq_filename, p3utr_seq_filename,
                 thesaurus_filename, nw_factories,
                 search_distances=SEARCH_DISTANCES,
                 scan_distances=SCAN_DISTANCES):
        """Creates the organism"""
        organism.OrganismBase.__init__(self, 'hsa', nw_factories)
        self.__prom_seq_filename = prom_seq_filename
        self.__p3utr_seq_filename = p3utr_seq_filename
        self.__thesaurus_filename = thesaurus_filename
        self.__search_distances = search_distances
        self.__scan_distances = scan_distances

        # lazy-loaded values
        self.__synonyms = None
        self.__p3utr_seqs = None
        self.__prom_seqs = None

    def species(self):
        """Retrieves the species of this object"""
        return 'hsa'

    def is_eukaryote(self):
        """Determines whether this object is an eukaryote"""
        return False

    def sequences_for_genes_search(self, gene_aliases, seqtype):
        """retrieve the sequences for the specified"""
        distance = self.__search_distances[seqtype]
        if seqtype == 'p3utr':
            return self.__get_p3utr_seqs(gene_aliases, distance)
        else:
            return self.__get_promoter_seqs(gene_aliases, distance)

    def sequences_for_genes_scan(self, gene_aliases, seqtype):
        """retrieve the sequences for the specified"""
        distance = self.__scan_distances[seqtype]
        if seqtype == 'p3utr':
            return self.__get_p3utr_seqs(gene_aliases, distance)
        else:
            return self.__get_promoter_seqs(gene_aliases, distance)

    def __get_p3utr_seqs(self, gene_aliases, distance):
        """Retrieves genomic sequences from the 3" UTR set"""
        print "GET_P3UTR SEQS, distance: ", distance
        if self.__p3utr_seqs == None:
            dfile = util.DelimitedFile.read(self.__p3utr_seq_filename, sep=',')
            self.__p3utr_seqs = {}
            for line in dfile.lines():
                self.__p3utr_seqs[line[0].upper()] = line[1]
        result = {}
        for alias in gene_aliases:
            if alias in self.thesaurus():
                gene = self.thesaurus()[alias]
                if gene in self.__p3utr_seqs:
                    result[gene] = self.__p3utr_seqs[gene]
                else:
                    #logging.warn("Gene '%s' not found in 3' UTRs", gene)
                    pass
            else:
                #logging.warn("Alias '%s' not in thesaurus !", alias)
                pass
        return result

    def __get_promoter_seqs(self, gene_aliases, distance):
        """Retrieves genomic sequences from the promoter set"""
        logging.info("GET PROMOTER SEQS, # GENES: %d", len(gene_aliases))
        if self.__prom_seqs == None:
            dfile = util.DelimitedFile.read(self.__prom_seq_filename, sep=',')
            self.__prom_seqs = {}
            for line in dfile.lines():
                self.__prom_seqs[line[0].upper()] = line[1]
        result = {}
        for alias in gene_aliases:
            if alias in self.thesaurus():
                gene = self.thesaurus()[alias]
                if gene in self.__prom_seqs:
                    seq = self.__prom_seqs[gene]
                    # result[gene] = st.subsequence(seq, distance[0],
                    #                               distance[1])
                    result[gene] = seq
                else:
                    #logging.warn("Gene '%s' not found in promoter seqs", gene)
                    pass
            else:
                #logging.warn("Alias '%s' not in thesaurus !", alias)
                pass
        logging.info("sequences all retrieved")
        return result

    def thesaurus(self):
        """Reads the synonyms from the provided CSV file"""
        if not self.__synonyms:
            self.__synonyms = thesaurus.create_from_delimited_file2(
                self.__thesaurus_filename)
        return self.__synonyms


######################################################################
##### Configuration
######################################################################

class CMonkeyConfiguration:
    def __init__(self, matrix_filename, num_iterations=NUM_ITERATIONS,
                 cache_dir=CACHE_DIR):
        """create instance"""
        logging.basicConfig(format=LOG_FORMAT,
                            datefmt='%Y-%m-%d %H:%M:%S',
                            level=logging.DEBUG)
        if not os.path.exists(cache_dir):
            os.mkdir(cache_dir)
        self.__cache_dir = cache_dir
        self.__matrix_filename = matrix_filename
        self.__num_iterations = num_iterations

        self.__matrix = None
        self.__membership = None
        self.__organism = None
        self.__row_scoring = None
        self.__column_scoring = None

    def num_iterations(self):
        """returns the number of iterations"""
        return self.__num_iterations

    def cache_dir(self):
        """returns the cache directory"""
        return self.__cache_dir

    def matrix(self):
        """returns the input matrix"""
        if self.__matrix == None:
            self.__matrix = read_matrix(self.__matrix_filename)
        return self.__matrix

    def membership(self):
        """returns the seeded membership"""
        if self.__membership == None:
            self.__membership = make_membership(self.matrix())
        return self.__membership

    def organism(self):
        """returns the organism object to work on"""
        if self.__organism == None:
            self.__organism = make_organism()
        return self.__organism

    def row_scoring(self):
        if self.__row_scoring == None:
            self.__row_scoring = make_gene_scoring_func(self.organism(),
                                                         self.membership(),
                                                         self.matrix())
        return self.__row_scoring

    def column_scoring(self):
        if self.__column_scoring == None:
            self.__column_scoring = microarray.ColumnScoringFunction(
                self.membership(), self.matrix())
        return self.__column_scoring


def read_controls():
    """reads the controls file"""
    with open(CONTROLS_FILE) as infile:
        return [line.strip() for line in infile.readlines()]


def read_rug(pred):
    """reads the rug file"""
    infile = util.DelimitedFile.read(RUG_FILE, sep=',', has_header=False)
    return list(set([row[0] for row in infile.lines() if pred(row)]))


def read_matrix(filename):
    """reads the data matrix from a file"""
    controls = read_controls()
    rug = read_rug(lambda row: row[1] in RUG_PROPS)
    columns_to_use = list(set(rug + controls))

    # pass the column filter as the first filter to the DataMatrixFactory,
    # so normalization will be applied to the submatrix
    matrix_factory = dm.DataMatrixFactory([
            lambda matrix: matrix.submatrix_by_name(
                column_names=columns_to_use)])
    infile = util.DelimitedFile.read(filename, sep=',', has_header=True,
                                     quote="\"")
    matrix = matrix_factory.create_from(infile)

    column_groups = {1: range(matrix.num_columns())}
    select_rows = select_probes(matrix, 2000, column_groups)
    matrix = matrix.submatrix_by_rows(select_rows)
    return intensities_to_ratios(matrix, controls, column_groups)


def make_membership(matrix):
    """returns a seeded membership"""
    return memb.ClusterMembership.create(
        matrix.sorted_by_row_name(),
        memb.make_kmeans_row_seeder(NUM_CLUSTERS),
        microarray.seed_column_members,
        num_clusters=NUM_CLUSTERS)


def make_organism():
    """returns a human organism object"""
    nw_factories = [stringdb.get_network_factory3('human_data/string.csv')]
    organism = Human(PROM_SEQFILE, P3UTR_SEQFILE, THESAURUS_FILE,
                     nw_factories)
    return organism


def make_gene_scoring_func(organism, membership, matrix):
    """setup the gene-related scoring functions here
    each object in this array supports the method
    compute(organism, membership, matrix) and returns
    a DataMatrix(genes x cluster)
    """
    row_scoring = microarray.RowScoringFunction(membership, matrix,
                                                lambda iteration: ROW_WEIGHT)

    sequence_filters = []
    meme_suite = meme.MemeSuite430()
    motif_scoring = motif.ScoringFunction(
        organism,
        membership,
        matrix,
        meme_suite,
        sequence_filters=sequence_filters,
        pvalue_filter=motif.make_min_value_filter(-20.0),
        seqtype='promoter',
        weight_func=lambda iteration: 0.0,
        interval=0)

    network_scoring = nw.ScoringFunction(organism, membership, matrix,
                                         lambda iteration: 0.0, 7)

    return memb.ScoringFunctionCombiner([row_scoring, motif_scoring,
                                         network_scoring])
