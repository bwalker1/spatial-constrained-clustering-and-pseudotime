# -*- coding: utf-8 -*-
from python_codes.util.config import args
from python_codes.visualize.chicken import *

if __name__ == "__main__":
    #annotation_pipeline(args)
    lineage_pipeline(args, all_lineage=True)
    #expr_analysis_pipeline(args)