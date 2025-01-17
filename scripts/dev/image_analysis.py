#!/bin/env python
from __future__ import print_function
import os
import sys
import multiprocessing as mp
import argparse
import time
import datetime
from dateutil.parser import parse as dt_parser
import sqlite3
import re
from subprocess import call

# Parse command-line arguments
###########################################
def options():
  """Parse command line options.
    
  Args:
    
  Returns:
    argparse object.
  Raises:
    IOError: if dir does not exist.
    IOError: if pipeline does not exist.
    IOError: if the metadata file SnapshotInfo.csv does not exist in dir when flat is False.
    ValueError: if adaptor is not phenofront or dbimportexport.
    ValueError: if a metadata field is not supported.
  """
  # Job start time
  start_time = datetime.datetime.now().strftime('%Y-%m-%d_%H:%M:%S')
  print("Starting run " + start_time + '\n', file=sys.stderr)
  
  # These are metadata types that PlantCV deals with.
  # Values are default values in the event the metadata is missing
  valid_meta = {
    # Camera settings
    'camera' : 'none',
    'imgtype' : 'none',
    'zoom' : 'none',
    'exposure': 'none',
    'gain' : 'none',
    'frame' : 'none',
    'lifter' : 'none',
    # Date-Time
    'timestamp' : 'none',
    # Sample attributes
    'id' : 'none',
    'plantbarcode' : 'none',
    'treatment' : 'none',
    'cartag' : 'none',
    # Experiment attributes
    'measurementlabel' : 'none',
    # Other
    'other' : 'none'
  }
  parser = argparse.ArgumentParser(description="Parallel imaging processing with PlantCV.", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
  parser.add_argument("-d", "--dir", help="Input directory containing images or snapshots.", required=True)
  parser.add_argument("-a", "--adaptor", help="Image metadata reader adaptor. PhenoFront metadata is stored in a CSV file and the image file name. For the filename option, all metadata is stored in the image file name. Current adaptors: phenofront, image", default="phenofront")
  parser.add_argument("-p", "--pipeline", help="Pipeline script file.", required=True)
  parser.add_argument("-s", "--db", help="SQLite database file name.", required=True)
  parser.add_argument("-i", "--outdir", help="Output directory for images. Not required by all pipelines.", default=".")
  parser.add_argument("-T", "--cpu", help="Number of CPU to use.", default=1, type=int)
  parser.add_argument("-c", "--create", help="Create output database (SQLite). Default behaviour adds to existing database. Warning: activating this option will delete an existing database!", default=False, action="store_true")
  parser.add_argument("-m", "--roi", help="ROI/mask image. Required by some pipelines (vis_tv, flu_tv).", required=False)
  parser.add_argument("-D", "--dates", help="Date range. Format: YYYY-MM-DD-hh-mm-ss_YYYY-MM-DD-hh-mm-ss. If the second date is excluded then the current date is assumed.", required=False)
  parser.add_argument("-t", "--type", help="Image format type (extension).", default="png")
  parser.add_argument("-r", "--random", help="Select a random set of images from the input directory.", default=False, action="store_true")
  parser.add_argument("-n", "--number", help="Number of random images to test. Only used with -r/--random.", default=10)
  parser.add_argument("-l", "--deliminator", help="Image file name metadata deliminator character.", default='_')
  parser.add_argument("-f", "--meta", help="Image file name metadata format. List valid metadata fields separated by the deliminator (-l/--deliminator). Valid metadata fields are: " + ', '.join(map(str, list(valid_meta.keys()))), default='imgtype_camera_frame_zoom_id')
  parser.add_argument("-M", "--match", help="Restrict analysis to images with metadata matching input criteria. Input a metadata:value comma-separated list. This is an exact match search. E.g. imgtype:VIS,camera:SV,zoom:z500", required=False)
  args = parser.parse_args()
  
  if not os.path.exists(args.dir):
    raise IOError("Directory does not exist: {0}".format(args.dir))
  if not os.path.exists(args.pipeline):
    raise IOError("File does not exist: {0}".format(args.pipeline))
  if args.adaptor is 'phenofront':
    if not os.path.exists(args.dir + '/SnapshotInfo.csv'):
      raise IOError("The snapshot metadata file SnapshotInfo.csv does not exist in {0}. Perhaps you meant to use a different adaptor?".format(args.dir))
  if not os.path.exists(args.outdir):
    raise IOError("Directory does not exist: {0}".format(args.outdir))
  
  args.jobdir = start_time
  try:
    os.makedirs(args.jobdir)
  except IOError as e:
    raise IOError("{0}: {1}".format(e.strerror, args.jobdir))
  
  if args.adaptor != 'phenofront' and args.adaptor != 'filename':
    raise ValueError("Adaptor must be either phenofront or filename")
  
  if args.dates:
    dates = args.dates.split('_')
    if len(dates) == 1:
      # End is current time
      dates.append(datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S'))
    start = map(int, dates[0].split('-'))
    end = map(int, dates[1].split('-'))
    # Convert start and end dates to Unix time
    start_td = datetime.datetime(*start) - datetime.datetime(1970,1,1)
    end_td = datetime.datetime(*end) - datetime.datetime(1970,1,1)
    args.start_date = (start_td.days * 24 * 3600) + start_td.seconds
    args.end_date = (end_td.days * 24 * 3600) + end_td.seconds
  else:
    end = datetime.datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
    end_list = map(int, end.split('-'))
    end_td = datetime.datetime(*end_list) - datetime.datetime(1970,1,1)
    args.start_date = 1
    args.end_date = (end_td.days * 24 * 3600) + end_td.seconds
  
  args.valid_meta = valid_meta
  args.start_time = start_time
  
  # Image filename metadata structure
  fields = args.meta.split(args.deliminator)
  structure = {}
  for i, field in enumerate(fields):
    structure[field] = i
  args.fields = structure
  
  # Are the user-defined metadata valid?
  for field in args.fields:
    if field not in args.valid_meta:
      raise ValueError("The field {0} is not a currently supported metadata type.".format(field))
  
  # Metadata restrictions
  args.imgtype = {}
  if args.match is not None:
    pairs = args.match.split(',')
    for pair in pairs:
      key, value = pair.split(':')
      args.imgtype[key] = value
  else:
    args.imgtype = None
    
  return args
###########################################

# Main
###########################################
def main():
  """Main program.
      
  Args:
    
  Returns:
    
  Raises:
  
  """
  
  # Get options
  args = options()
  
  # Variables
  ###########################################
  meta = {}
  
  # Get this script's path
  exedir = os.path.abspath(os.path.dirname(sys.argv[0]))
  #db_schema = exedir + '/../../include/results.sql'
  
  # Get command
  command = ' '.join(map(str, sys.argv))
  
  # Database upload file name prefix
  # Use user inputs to make filenames
  prefix = 'plantcv'
  if args.imgtype is not None:
    kv_list = []
    for key in args.imgtype:
      kv_list.append(key + str(args.imgtype[key]))
    prefix = prefix + '_' + '_'.join(map(str, kv_list))  
  if (args.dates):
    prefix = prefix + '_' + args.dates
  ###########################################
  
  # Open log files
  fail_log = file_writer(prefix + '_failed_images_' + args.start_time + '.log')
  error_log = file_writer(prefix + '_errors_' + args.start_time + '.log')
  
  # Open intermediate database files
  runinfo_file = file_writer(prefix + '_runinfo.tab')
  args.metadata_file = file_writer(prefix + '_metadata.tab')
  args.analysis_images_file = file_writer(prefix + '_analysis_images.tab')
  args.features_file = file_writer(prefix + '_features.tab')
  args.signal_file = file_writer(prefix + '_signal.tab')
  
  # Database setup
  ###########################################
  args = db_connect(args)
  ###########################################
  
  # Run info
  ###########################################
  # Next run ID
  args.run_id += 1
  
  runinfo_file.write("|".join(map(str, (args.run_id, args.start_time, command))) + '\n')
  ###########################################
  
  # Read image file names
  ###########################################
  if args.adaptor == 'filename':
    # Input directory contains images where the file name contains all metadata
    meta = filename_parser(args)
  elif args.adaptor == 'phenofront':
    # Input directory is in PhenoFront snapshot format with subdirectories for each snapshot.
    # Metadata is stored in a CSV file.
    meta = phenofront_parser(args)
  ###########################################
  
  # Process images
  ###########################################
  # Job builder start time
  job_builder_start_time = time.time()
  print("Building job list... ", file=sys.stderr)
  jobs = job_builder(args, meta)
  # Job builder clock time
  job_builder_clock_time = time.time() - job_builder_start_time
  print("took " + str(job_builder_clock_time) + '\n', file=sys.stderr)
  
  # Parallel image processing time
  multi_start_time = time.time()
  print("Processing images... ", file=sys.stderr)
  p = mp.Pool(processes=args.cpu)
  p.map(process_images_multiproc, jobs)
  # Parallel clock time
  multi_clock_time = time.time() - multi_start_time
  print("took " + str(multi_clock_time) + '\n', file=sys.stderr)
  
  ###########################################
  
  # Compile image analysis results
  ###########################################
  # Process results start time
  process_results_start_time = time.time()
  print("Processing results... ", file=sys.stderr)
  process_results(args)
  # Process results clock time
  process_results_clock_time = time.time() - process_results_start_time
  print("took " + str(process_results_clock_time) + '\n', file=sys.stderr)
  ###########################################
  
  # Cleanup
  ###########################################
  runinfo_file.close()
  args.metadata_file.close()
  args.features_file.close()
  args.signal_file.close()
  args.analysis_images_file.close()
  fail_log.close()
  error_log.close()
  args.connect.close()
  ###########################################
  
  # Load database
  ###########################################
  call("sqlite3 " + args.db + " '.import " + runinfo_file.name + " runinfo'", shell=True)
  call("sqlite3 " + args.db + " '.import " + args.metadata_file.name + " metadata'", shell=True)
  call("sqlite3 " + args.db + " '.import " + args.features_file.name + " features'", shell=True)
  call("sqlite3 " + args.db + " '.import " + args.analysis_images_file.name + " analysis_images'", shell=True)
  call("sqlite3 " + args.db + " '.import " + args.signal_file.name + " signal'", shell=True)
  ###########################################
  
###########################################

# Open a file for writing
###########################################
def file_writer(filename):
  """
  Open a file for writing.
  
  Args:
    filename: (string) the name of the path/file to open for writing.
  Returns:
    file object.
  Raises:
    IOError: If filename is not writeable.
  """
  try:
    fileobj = open(filename, 'w')
  except IOError as e:
    raise IOError("{0}: {1}".format(e.strerror, filename))
  return(fileobj)
###########################################

# Print a message and exit the program
###########################################
def exit_message(message):
  """
  Print error message and exit program.
  
  Args:
    message: (string) the error message to print.
  Returns:
    
  Raises:
  
  """
  sys.exit(message)
###########################################

# Dictionary factory for SQLite query results
###########################################
def dict_factory(cursor, row):
  """
  Replace the row_factory result constructor with a dictionary constructor.
  
  Args:
    cursor: (object) the sqlite3 database cursor object.
    row: (list) a result list.
  Returns:
    d: (dictionary) sqlite3 results dictionary.
  Raises:
  
  """
  d = {}
  for idx, col in enumerate(cursor.description):
    d[col[0]] = row[idx]
  return d
###########################################

# Connect to output database
###########################################
def db_connect(args):
  """
  Connect to the output database, initialize if requested.
  Currently supports SQLite3.
  
  Args:
    args: (object) argsparse object.
  Returns:
    args: (object) argparse object with the following added:
      args.sq: Database cursor.
      args.run_id: Last run ID.
      args.image_id: Last image ID.
  Raises:
    IOError: If schema does not exist or is not readable.
  """
  
  # Delete the existing database if create is true
  if args.create:
    if os.path.isfile(args.db):
      response = raw_input("WARNING: SQLite database file $sqldb already exists are you sure you want to delete it? (y/n): ")
      if response == 'y':
        os.remove(args.db)
      else:
        exit_message("Okay, stopping")
  
  # Connect to the database
  args.connect = sqlite3.connect(args.db)
  
  # Replace the row_factory result constructor with a dictionary constructor
  args.connect.row_factory = dict_factory
  
  # Change the text output format from unicode to UTF-8
  args.connect.text_factory=str
  
  # Database handler
  args.sq = args.connect.cursor()
  
  # Run and image IDs
  args.run_id = 0
  args.image_id = 0
  
  if not args.create:
    # Get the last run ID
    for row in args.sq.execute('SELECT MAX(run_id) as max FROM runinfo'):
      if row['max'] is not None:
        args.run_id = row['max']
      
    # Get the last run ID
    for row in args.sq.execute('SELECT MAX(image_id) as max FROM metadata'):
      if row['max'] is not None:
        args.image_id = row['max']
  
  return(args)
###########################################

# Reads images from a single directory in
# LemnaTec DBImportExport format
###########################################
def filename_parser(args):
  """
  Reads metadata from file names.
  
  Args:
    args: (object) argparse object.
  Returns:
    meta: image metadata object.
  Raises:
    IOError: if an image file does not exist.
  """
  # Metadata data structure
  meta = {}
  
  # Compile regular expression to remove image file extensions
  pattern = '\.' + args.type + '$'
  ext = re.compile(pattern, re.IGNORECASE)
  
  # Walk through the input directory and find images that match input criteria
  for (dirpath, dirnames, filenames) in os.walk(args.dir):
    for filename in filenames:
      # Is filename and image?
      is_img = ext.search(filename)
      # If filename is an image, parse the metadata
      if is_img is not None:
        # Remove the file extension
        prefix = ext.sub('', filename)
        metadata = prefix.split(args.deliminator)
        
        # Image metadata
        img_meta = {}
        img_meta['path'] = dirpath
        img_pass = 1
        # For each of the type of metadata PlantCV keeps track of
        for field in args.valid_meta:
          # If the same metadata is found in the image filename, store the value
          if field in args.fields:
            meta_value = metadata[args.fields[field]]
            # If the metadata type has a user-provided restriction
            if field in args.imgtype:
              # If the input value does not match the image value, fail the image
              if meta_value != args.imgtype[field]:
                img_pass = 0
            img_meta[field] = meta_value
          # Or use the default value
          else:
            img_meta[field] = args.valid_meta[field]
        
        # If the image meets the user's criteria, store the metadata
        if img_pass == 1:
          meta[filename] = img_meta
  
  return(meta)

###########################################

# Reads images from a structured directory in
# PhenoFront format
###########################################
def phenofront_parser(args):
  """
  Reads metadata in PhenoFront format.
  
  Args:
    args: (object) argparse object.
  Returns:
    meta: image metadata object.
  Raises:
    
  """
  # Metadata data structure
  meta = {}
  
  # Open the SnapshotInfo.csv file
  csvfile  = open (args.dir + '/SnapshotInfo.csv', 'rU')
  
  # Read the first header line
  header = csvfile.readline()
  header = header.rstrip('\n')
  
  # Remove whitespace from the field names
  header = header.replace(" ", "")
  
  # Table column order
  cols = header.split(',')
  colnames = {}
  for i, col in enumerate(cols):
    colnames[col] = i
  
  # Read through the CSV file
  for row in csvfile:
    row = row.rstrip('\n')
    data = row.split(',')
    img_list = data[colnames['tiles']]
    img_list = img_list[:-1]
    imgs = img_list.split(';')
    for img in imgs:
      if len(img) != 0:
        dirpath = args.dir + '/snapshot' + data[colnames['id']]
        filename =  img + '.' + args.type
        if not os.path.exists(dirpath + '/' + filename):
          raise IOError("Something is wrong, file {0}/{1} does not exist".format(dirpath, filename))
        # Metadata from image file name
        metadata = img.split(args.deliminator)
        # Not all images in a directory may have the same metadata structure only keep those that do
        if len(metadata) == len(args.fields.keys()):
          # Image metadata
          img_meta = {}
          img_meta['path'] = dirpath
          img_pass = 1
          # For each of the type of metadata PlantCV keeps track of
          for field in args.valid_meta:
            # If the same metadata is found in the image filename, store the value
            if field in args.fields:
              meta_value = metadata[args.fields[field]]
              # If the metadata type has a user-provided restriction
              if field in args.imgtype:
                # If the input value does not match the image value, fail the image
                if meta_value != args.imgtype[field]:
                  img_pass = 0
              img_meta[field] = meta_value
            # If the same metadata is found in the CSV file, store the value
            elif field in colnames:
              meta_value = data[colnames[field]]
              # If the metadata type has a user-provided restriction
              if field in args.imgtype:
                # If the input value does not match the image value, fail the image
                if meta_value != args.imgtype[field]:
                  img_pass = 0
              img_meta[field] = meta_value
            # Or use the default value
            else:
                img_meta[field] = args.valid_meta[field]
            
          # If the image meets the user's criteria, store the metadata
          if img_pass == 1:
            meta[filename] = img_meta
  
  return(meta)
###########################################

# Process images using multiprocessing
###########################################
def process_images_multiproc(jobs):
  for job in jobs:
    os.system(job)

###########################################

# Build job list
###########################################
def job_builder(args, meta):
  """
  Build a list of image processing jobs.
  
  Args:
    args: (object) argparse object.
    meta: metadata data structure.
  Returns:
    
  Raises:
    
  """
  # Overall job stack. List of list of jobs
  job_stack = []
  
  # Jobs/CPU (INT): divide the number of images by the number of requested CPU resources
  jobs_per_cpu = len(meta) / args.cpu
  
  # Get the list of images
  images = list(meta.keys())
  
  # For each image
  for img in images:
    # Create an output file to store the image processing results and populate with metadata
    outfile = file_writer("./{0}/{1}.txt".format(args.jobdir, img))
    outfile.write('\t'.join(map(str, ("META", "image", meta[img]['path'] + '/' + img))) + '\n')
    # Valid metadata
    for m in list(args.valid_meta.keys()):
      outfile.write('\t'.join(map(str, ("META", m, meta[img][m]))) + '\n')
    outfile.close()
  
  # Build the job stack
  # The first n - 1 CPUs will get INT jobs_per_cpu
  # The last CPU will get the remainder
  job = 0
  # For the first n - 1 CPU
  for c in range(1, args.cpu):
    # List of jobs for this CPU
    jobs = []
    
    # For each job/CPU
    for j in range(0, jobs_per_cpu):
      # Add job to list
      job_str = "{0} --image {1}/{2} --outdir {3} >> ./{4}/{5}.txt".format(args.pipeline, meta[images[job]]['path'], images[job], args.outdir, args.jobdir, images[job])
      jobs.append(job_str)
      
      # Increase the job counter by 1
      job = job + 1
    
    # Add the CPU job list to the job stack
    job_stack.append(jobs)
  
  # Add the remaining jobs to the last CPU
  jobs = []
  for j in range(job, len(images)):
    # Add job to list
    job_str = "{0} --image {1}/{2} --outdir {3} >> ./{4}/{5}.txt".format(args.pipeline, meta[images[j]]['path'], images[j], args.outdir, args.jobdir, images[j])
    jobs.append(job_str)
  # Add the CPU job list to the job stack
  job_stack.append(jobs)
  
  return(job_stack)
  
###########################################

# Process results. Parse individual image output files.
###########################################
def process_results(args):
  """
  Get results from individual files.
  Parse the results and recompile for SQLite.
  
  Args:
    args: (object) argparse object.
  Returns:
    
  Raises:
    
  """
  # Add a header to each output file
  # Metadata table
  metadata_fields = ['image_id', 'run_id']
  metadata_fields.extend(args.valid_meta.keys())
  #args.metadata_file.write('#' + '\t'.join(map(str, metadata_fields)) + '\n')
  
  # Feature data table
  feature_fields = ['area', 'hull-area', 'solidity', 'perimeter', 'width', 'height',
                  'longest_axis', 'center-of-mass-x', 'center-of-mass-y', 'hull_vertices',
                  'in_bounds']
  opt_feature_fields = ['y-position', 'height_above_bound', 'height_below_bound',
                        'above_bound_area', 'percent_above_bound_area', 'below_bound_area',
                        'percent_below_bound_area']
  
  #args.features_file.write('#' + '\t'.join(map(str, feature_fields + opt_feature_fields)) + '\n')
  
  # Signal channel data table
  signal_fields = ['bin-number', 'channel_name', 'values']
  
  #bin-number	blue	green	red	lightness	green-magenta	blue-yellow	hue	saturation	value
  
  # Initialize the database with the schema template if create is true
  if args.create:
    # Create SQL structure based on accepted metadata and features
    args.sq.execute('CREATE TABLE IF NOT EXISTS `runinfo` (`run_id` INTEGER PRIMARY KEY, `datetime` INTEGER NOT NULL, `command` TEXT NOT NULL);')
    args.sq.execute('CREATE TABLE IF NOT EXISTS `metadata` (`image_id` INTEGER PRIMARY KEY, `run_id` INTEGER NOT NULL, `' + '` TEXT NOT NULL, `'.join(map(str, metadata_fields[2:])) + '` TEXT NOT NULL);')
    args.sq.execute('CREATE TABLE IF NOT EXISTS `features` (`image_id` INTEGER PRIMARY KEY, `' + '` TEXT NOT NULL, `'.join(map(str, feature_fields + opt_feature_fields)) + '` TEXT NOT NULL);')
    args.sq.execute('CREATE TABLE IF NOT EXISTS `analysis_images` (`image_id` INTEGER NOT NULL, `type` TEXT NOT NULL, `image_path` TEXT NOT NULL);')
    args.sq.execute('CREATE TABLE IF NOT EXISTS `signal` (`image_id` INTEGER NOT NULL, `' + '` TEXT NOT NULL, `'.join(map(str, signal_fields)) + '` TEXT NOT NULL);')
  
  # Walk through the image processing job directory and process data from each file
  for (dirpath, dirnames, filenames) in os.walk(args.jobdir):
    for filename in filenames:
      meta = {}
      images = {}
      features = []
      feature_data = {}
      signal = []
      signal_data = {}
      boundary = []
      boundary_data = {}
      # Open results file
      with open(dirpath + '/' + filename) as results:
        # For each line in the file
        for row in results:
          # Remove the newline character
          row = row.rstrip('\n')
          # Split the line by tab characters
          cols = row.split('\t')
          # If the data is of class meta, store in the metadata dicitonary
          if cols[0] == 'META':
            meta[cols[1]] = cols[2]
          # If the data is of class image, store in the image dictionary
          elif cols[0] == 'IMAGE':
            images[cols[1]] = cols[2]
          # If the data is of class shapes, store in the shapes dictionary
          elif cols[0] == 'HEADER_SHAPES':
            features = cols
          elif cols[0] == 'SHAPES_DATA':
            for i, datum in enumerate(cols):
              if i > 0:
                feature_data[features[i]] = datum
          # If the data is of class histogram/signal, store in the signal dictionary
          elif cols[0] == 'HEADER_HISTOGRAM':
            signal = cols
          elif cols[0] == 'HISTOGRAM_DATA':
            for i, datum in enumerate(cols):
              if i > 0:
                signal_data[signal[i]] = datum
          # If the data is of class boundary (horizontal rule), store in the boundary dictionary
          elif 'HEADER_BOUNDARY' in cols[0]:
            boundary = cols
            # Temporary hack
            boundary_data['y-position'] = cols[0].replace('HEADER_BOUNDARY', '')
          elif cols[0] == 'BOUNDARY_DATA':
            for i, datum in enumerate(cols):
              if i > 0:
                boundary_data[boundary[i]] = datum
      
      # Check to see if the image failed, if not continue
      if (len(feature_data) != 0):
        # Convert image datetime to unix time
        timestamp = dt_parser(meta['timestamp'])
        time_delta = timestamp - datetime.datetime(1970,1,1)
        unix_time = (time_delta.days * 24 * 3600) + time_delta.seconds
        
        # Print the image metadata to the aggregate output file
        args.image_id += 1
        meta['image_id'] = args.image_id
        meta['run_id'] = args.run_id
        meta['unixtime'] = unix_time
        
        meta_table = []
        for field in metadata_fields:
          meta_table.append(meta[field])
        
        args.metadata_file.write('|'.join(map(str, meta_table)) + '\n')
        
        # Print the image feature data to the aggregate output file
        feature_data['image_id'] = args.image_id
        
        # Boundary data is optional, if it's not there we need to add in placeholder data
        if len(boundary_data) == 0:
          for field in opt_feature_fields:
            boundary_data[field] = 0
        feature_data.update(boundary_data)
        
        feature_table = [args.image_id]
        for field in feature_fields + opt_feature_fields:
          feature_table.append(feature_data[field])
        
        args.features_file.write('|'.join(map(str, feature_table)) + '\n')
        
        # Print the analysis image data to the aggregate output file
        for img_type in images:
          args.analysis_images_file.write('|'.join(map(str, (args.image_id, img_type, images[img_type]))) + '\n')
        
        # Print the image signal data to the aggregate output file
        for key in signal_data.keys():
          if key != 'bin-number':
            signal_data[key] = signal_data[key].replace('[','')
            signal_data[key] = signal_data[key].replace(']','')
            signal_table = [args.image_id, signal_data['bin-number'], key, signal_data[key]]
            args.signal_file.write('|'.join(map(str, signal_table)) + '\n')
      

if __name__ == '__main__':
  main()