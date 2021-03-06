import os
import sys
import argparse
from argparse import ArgumentParser
import pandas as pd
import time
from subprocess import call
import time
from mrmr_wrapper import call_mrmr_routine
from mrmr_wrapper import remove_mrmr_input_folder_to_clean_up_space
from lasso_random_forest import obtain_feature_importance_from_rf
from lasso_random_forest import return_best_rf_regressor
from lasso_random_forest import run_lasso_on_input
from lasso_random_forest import obtain_feature_importance_from_lasso

from clean_up_mrmr_output import aggregate_mrmr_results_and_pickle_dictionary
from clean_up_mrmr_output import write_out_rankings
from clean_up_mrmr_output import get_list_of_files
from clean_up_mrmr_output import convert_to_float

from utils import transform_column_float
from utils import sample_data_frame_return_x_y_column_name
from utils import pickle_this_variable_with_this_name_to_this_folder

from orthogonal_projection import orthogonal_variable_selection_cannot_query_black_box
from orthogonal_projection import aggregate_orthogonal_rankings

from graphing_results import *


import pickle
import logging

from multiprocessing import Process

"""
in fairness rating folder 

python fairml.py --file=../data/processed_data_sets/turkey_credit_individual_data_with_pd_limit.csv --target=credit_limit
"""

def purge():
	#I should double check using this
	call(["sudo","purge"])
	return None

def build_parser():
	parser = ArgumentParser(
		description="Runs FairML and associated scripts")
	parser.add_argument(
		'--file', type=str, help='input file', dest='input_file', 
		required=True)
	parser.add_argument(
		'--target', type=str,
		help='column name of target variable', dest='target', required=True)
	parser.add_argument(
		'--bootstrap', type=int,
		help='No. of boostrap iterations', dest='no_bootstrap')
	parser.add_argument(
		'--generate_pdf', type=bool, default=False,
		help='generate explanatory pdf', dest='generate_pdf')
	parser.add_argument(
		'--data_bias', type=bool, default=False,
		help='bias vs accuracy plot', dest='data_bias')
	parser.add_argument(
		'--explain', type=bool, default=False,
		help='partial dependence plot for top features', 
		dest='explain_top_features')
	parser.add_argument(
		'--sensitive', nargs='+', type=list,
		help='list of sensitive variables', 
		dest='sensitive_variable_list')

	#add argument for sensitive variables
	#list of string names
	options = parser.parse_args()
	return options


def create_analysis_folders(options):

	print "setting up variables"
	now = time.time() 
	now_date_format  = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(now))
	new_date = "_".join(now_date_format.split())
	name_of_folder = "fairml_analysis_" + new_date

	text_for_read_me = "This folder includes results for the analysis of the file {0}. \
						This analysis was conducted by FairML at {1}.".format(options.input_file, now_date_format)

	#create the names of the folders
	mrmr_output = name_of_folder + "/mrmr_output"
	ranking_results = name_of_folder + "/ranking_results"
	plots = name_of_folder + "/plots"
	log_file = name_of_folder + "/log_file_" + str(now) + ".txt"
	mrmr_input = name_of_folder + "/mrmr_input"

	print "creating folders"
	#create the folders
	call(["mkdir", name_of_folder])
	call(["mkdir", mrmr_output])
	call(["mkdir", mrmr_input])
	call(["mkdir", ranking_results])
	call(["mkdir", plots])
	call(["touch", log_file])

	#initialize log files
	#will deal with log files later
	touch_file = name_of_folder + "/readme.txt"
	call(["touch", touch_file])
	e = "echo {0} >> {1}".format(text_for_read_me, touch_file)
	call(e, shell=True)

	return {"main_folder": name_of_folder, "mrmr_output": mrmr_output, "ranking_results" : ranking_results, 
			"plots": plots, "touch_file":touch_file, "mrmr_input":mrmr_input, "log_file":log_file}

def confirm_input_arguments_and_set_analysis_folders(options):

	print "checking input file"
	#check input file
	try:
		f = open(options.input_file)
	except IOError as e:
		print "I/O error({0}): {1}".format(e.errno, e.strerror)
	except:
		print "Unexpected error:", sys.exc_info()[0]
		raise

	print "checking if file can be read by pandas"
	#now read the file and check if target variable is in there. 
	try:
		full_input_data = pd.read_csv(filepath_or_buffer=options.input_file, sep=',')
	except:
		print "Unexpected error while reading the input file", sys.exc_info()[0]
		raise

	#now input file read as csv
	#get list of column names
	column_names = list(full_input_data.columns)
	duplicate_columns = len(column_names) - len(set(column_names))

	print "checking duplicate columns"
	#check for duplicate attributes
	if duplicate_columns > 0:
		raise "Your Input File has duplicate attributes, please remove duplicates"

	print "checking if specified target is in input file"
	#check if target is in list of column names
	column_names_lower = [a.lower() for a in column_names]
	if options.target.lower() not in column_names_lower:
		raise "Your input file does not contain the target variable that you specificied. Please check \
			 that you have the correct spelling "

	target_name_in_csv = column_names[column_names_lower.index(options.target.lower())]
	#check that the sensitive attributes are in the list of variables
	if options.sensitive_variable_list != None:
		sensitive = [a.lower() for a in options.sensitive_variable_list]
		for attribute in sensitive:
			if attribute not in column_names_lower:
				raise "Your input file does not contain the %s that you specificied. Please check \
			 	that you have the correct spelling ", attribute


	print "dropping n/a from dataframe"

	#drop rows with no na
	full_input_data.dropna(axis=0, how='any', inplace=True)


	print "converting all columns to floats"
	#now convert all the columns to float64
	for name in column_names:
		#print "we are working with {0} now".format(name)
		if full_input_data[name].dtype != float:
			full_input_data[name] = full_input_data[name].map(transform_column_float)

	print "done converting all columns to floats"

	print "creating analysis folders"
	#set up the relevant analysis folders. 
	folder_paths_and_target = create_analysis_folders(options)

	#now write input file for mrmr analysis
	#ricci_new_df.to_csv(path_or_buf="ricci_data_processed.csv", sep=',', index=False)

	where_to_write_mrmr_input = folder_paths_and_target["mrmr_input"] + "/input_file.csv"
	
	no_columns_in_df = full_input_data.shape[1]


	new_column_name = ['feature'+str(i) for i in range(no_columns_in_df)]
	new_full_input_data = full_input_data.copy()
	new_full_input_data.columns = new_column_name

	original_columns = list(full_input_data.columns)

	mapping_for_new_columns_names = {}
	for i in range(len(new_column_name)):
		mapping_for_new_columns_names[new_column_name[i]] = original_columns[i]

	#write out mapping to ranking
	mapping_name = "mrmr_column_feature_name_mapping.pickle"
	pickle_this_variable_with_this_name_to_this_folder(mapping_for_new_columns_names, folder_paths_and_target["ranking_results"] + "/" + mapping_name)

	new_full_input_data.to_csv(path_or_buf=where_to_write_mrmr_input , sep=',', index=False)

	#target name mrmr
	
	folder_paths_and_target["target_mrmr"] = new_column_name[original_columns.index(target_name_in_csv)]

	#and the name of target in the csv into the dictionary
	folder_paths_and_target["target"] = target_name_in_csv

	print "target name is " + folder_paths_and_target["target"]
	print "mrmr target name is " + folder_paths_and_target["target_mrmr"]
	return full_input_data, folder_paths_and_target


def run_mrmr_on_input_data_set(input_data_frame, options, analysis_file_paths):
	now = time.time()
	print "#######################################"
	print "Starting MRMR"
	print "#######################################"
	csv_input_mrmr = analysis_file_paths["mrmr_input"] + "/input_file.csv"
	call_mrmr_routine(csv_input_mrmr, analysis_file_paths["target_mrmr"], analysis_file_paths["mrmr_output"]+"/")
	aggregate_mrmr_results_and_pickle_dictionary(analysis_file_paths["mrmr_output"], analysis_file_paths["ranking_results"])
	#remove_mrmr_input_folder_to_clean_up_space(analysis_file_paths["mrmr_input"])
	print "Fitting MRMR and clean up took  ------>>> " + str(float(time.time() - now)/60.0) + " minutes!"


def run_random_forest_on_input_data_set(input_data_frame, options, analysis_file_paths, num_trees_hyperparameter, num_trees_final_clf, num_iterations):
	now = time.time()
	print "#######################################"
	print "Starting RANDOM FOREST"
	print "#######################################"

	#return_best_rf_regressor(df, target, num_trees_hyperparameter, num_trees_final_clf, num_iterations)
	#obtain_feature_importance_from_rf(clf, column_names, file_path)

	best_clf, column_list_for_fit_data = return_best_rf_regressor(input_data_frame, analysis_file_paths["target"], num_trees_hyperparameter, num_trees_final_clf, num_iterations)
	obtain_feature_importance_from_rf(best_clf, column_list_for_fit_data, analysis_file_paths["ranking_results"])
	print "Fitting Random Forest and clean up took  ------>>> " + str(float(time.time() - now)/60.0) + " minutes!"

def run_lasso_on_input_data_set(input_data_frame, analysis_file_paths):
	now = time.time()
	print "#######################################"
	print "Starting RANDOM FOREST"
	print "#######################################"

	clf, column_list = run_lasso_on_input(input_data_frame, analysis_file_paths["target"])
	obtain_feature_importance_from_lasso(clf, column_list, analysis_file_paths["ranking_results"])
	print "Fitting Lasso and clean up took  ----->>> " +  str(float(time.time() - now)/60.0) + " minutes!"

def run_orthogonal_ranking_no_black_box_on_input_data_set(input_data_frame, analysis_file_paths, non_linear, no_bootstrap_iter):

	#set number of samples

	num_samples = input_data_frame.shape[0]

	if (num_samples > 100000) :
		num_samples = 100000

	if non_linear > 0:
		if input_data_frame.shape[0] > 5000:
			num_samples = 5000

	#orthogonal_variable_selection_cannot_query_black_box(df, target, non_linear, no_bootstrap_iter, num_samples)
	orthogonal_results = orthogonal_variable_selection_cannot_query_black_box(input_data_frame, analysis_file_paths["target"], non_linear, no_bootstrap_iter, num_samples)
	print_result = aggregate_orthogonal_rankings(orthogonal_results, analysis_file_paths["ranking_results"])

	print "Finished Running Orthogonal Transformation"

def main():
	now = time.time()
	options = build_parser()
	purge()
	input_data_frame, analysis_file_paths = confirm_input_arguments_and_set_analysis_folders(options)

	font = {'family': 'Serif',
        'color':  'Black',
        'weight': 'normal',
        'size': 13,
        }

	title_font = {'family': 'Serif',
        'color':  'Black',
        'weight': 'semibold',
        'size': 15,
        }
	
	#calling random forest 
	#I should add this to the input arguments
	num_trees_hyperparameter = 15
	num_trees_final_clf = 90
	num_iterations_rf = 3

	#if non_linear > 1 then set no_bootstrap sampling 
	no_bootstrap_orthogonal_ranking = 10
	orthogonal_non_linear = 0 #if non_linear > 0, then it does non linear version. 

	#calling mrmr
	run_mrmr_on_input_data_set(input_data_frame, options, analysis_file_paths)
	purge()

	#run_random_forest_on_input_data_set(input_data_frame, options, analysis_file_paths, num_trees_hyperparameter, num_trees_final_clf, num_iterations)
	run_random_forest_on_input_data_set(input_data_frame, options, analysis_file_paths, num_trees_hyperparameter , num_trees_final_clf, num_iterations_rf)
	purge()

	#call lasso
	run_lasso_on_input_data_set(input_data_frame, analysis_file_paths)
	purge()

	#call orthogonal feature transformation
	run_orthogonal_ranking_no_black_box_on_input_data_set(input_data_frame, analysis_file_paths, orthogonal_non_linear, no_bootstrap_orthogonal_ranking)
	purge()

	print "Entire analysis took ------>>> " + str(float(time.time() - now)/60.0) + " minutes!"
	now = time.time()
	#now call the graphing components
	run_graphing_module(analysis_file_paths["ranking_results"], analysis_file_paths["plots"], \
					(10, 10), ["male", "female"], analysis_file_paths["target"], font, title_font)
	purge()

	print "Plotting took ------>>> " + str(float(time.time() - now)/60.0) + " minutes!"

if __name__ == '__main__':
	main()