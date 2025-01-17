from __future__ import print_function
import os, sys
import json
import sqlite3
import traceback
import argparse
from tqdm import tqdm
from itertools import product
from collections import defaultdict
import random
from datetime import datetime
import os
from math import ceil
import random
from typing import Optional
from pathlib import Path
import sys
from transformers import T5Tokenizer, T5ForConditionalGeneration
from datasets import load_dataset
import torch
import transformers
from peft import (
    LoraConfig,
    get_peft_model,
    get_peft_model_state_dict,
    prepare_model_for_int8_training,
    set_peft_model_state_dict,
)
from transformers import LlamaForCausalLM, LlamaTokenizer
# from process_sql import tokenize, get_schema, get_tables_with_alias, Schema, get_sql
import pandas as pd
import configparser
import logging
import re

import db2_connector
import ibm_db

######################################################################################################
# EX Match Logic
######################################################################################################
def error_handling(e):
    error ="None"
    if 'no such column' in e:
            error ="No such column"
    elif 'syntax error' in e:
            error = "Syntax error"
    elif 'no such table' in e:
            error = "No such table"
    elif 'ambiguous column name' in e:
            error = "Ambiguous column name"
    else:
        error = e
    return error

def reformat_query(query: str) -> str:
    t_stars = ["t1.*", "t2.*", "t3.*", "T1.*", "T2.*", "T3.*"]
    for ts in t_stars:
        query = query.replace(ts, "*")
    return query


def isValidSQL(sql, db):
    conn = sqlite3.connect(db)
    cursor = conn.cursor()
    try:
        cursor.execute(sql)
    except:
        return False
    return True

def unorder_row(row):
    return tuple(sorted(row, key=lambda x: str(x) + str(type(x))))

def quick_rej(result1, result2, order_matters):
    s1 = [unorder_row(row) for row in result1]
    s2 = [unorder_row(row) for row in result2]
    if order_matters:
        return s1 == s2
    else:
        return set(s1) == set(s2)
    
def get_constraint_permutation(tab1_sets_by_columns, result2):
    num_cols = len(result2[0])
    perm_constraints = [{i for i in range(num_cols)} for _ in range(num_cols)]
    if num_cols <= 3:
        return product(*perm_constraints)

    # we sample 20 rows and constrain the space of permutations
    for _ in range(20):
        random_tab2_row = random.choice(result2)

        for tab1_col in range(num_cols):
            for tab2_col in set(perm_constraints[tab1_col]):
                if random_tab2_row[tab2_col] not in tab1_sets_by_columns[tab1_col]:
                    perm_constraints[tab1_col].remove(tab2_col)
    return product(*perm_constraints)

def permute_tuple(element, perm):
    assert len(element) == len(perm)
    return tuple([element[i] for i in perm])

def multiset_eq(l1, l2):
    if len(l1) != len(l2):
        return False
    d = defaultdict(int)
    for e in l1:
        d[e] = d[e] + 1
    for e in l2:
        d[e] = d[e] - 1
        if d[e] < 0:
            return False
    return True

def result_eq_db2(result1, result2, order_matters):
    result = 'None'
    num_cols =0
    if len(result1) == 0 and len(result2) == 0:
        result = "same"
        return True,result
    
    if result1 == result2:
        result = "same"
        return True,result
        
    # if length is not the same, then they are definitely different bag of rows
    status =0
    if len(result1) != len(result2):
        if len(result1)==0:
            result = "P result zero"
        elif len(result2)==0:
            result = "Q result zero"
        elif len(result1) > len(result2):
            for res in result2:
                if res in result1:
                    status =1
            if status ==1:
                result = "Partial Match"
            else:
                result = "P result greater"
                
        elif len(result1) < len(result2):
            for res in result1:
                if res in result2:
                    status =1
            if status ==1:
                result = "Partial Match"
            else:   
                result = "Q result greater"    
        return False,result

    # unorder each row and compare whether the denotation is the same
    # this can already find most pair of denotations that are different
    if not quick_rej(result1, result2, order_matters):
        count =0
        for res in result2:
                if res in result1:
                    count =1
        if count ==1:
            result = "Partial Match"
        else:
            result = "order or result different"
        return False,result

    # the rest of the problem is in fact more complicated than one might think
    # we want to find a permutation of column order and a permutation of row order,
    # s.t. result_1 is the same as result_2
    # we return true if we can find such column & row permutations
    # and false if we cannot
    tab1_sets_by_columns = [{row[i] for row in result1} for i in range(num_cols)]
    
    return False,result



def result_eq(result1, result2, order_matters):
    result ="None"
    if len(result1) == 0 and len(result2) == 0:
        result = "same"
        return True,result

    # if length is not the same, then they are definitely different bag of rows
    status =0
    if len(result1) != len(result2):
        if len(result1)==0:
            result = "P result zero"
        elif len(result2)==0:
            result = "Q result zero"
        elif len(result1) > len(result2):
            for res in result2:
                if res in result1:
                    status =1
            if status ==1:
                result = "Partial Match"
            else:
                result = "P result greater"
                
        elif len(result1) < len(result2):
            for res in result1:
                if res in result2:
                    status =1
            if status ==1:
                result = "Partial Match"
            else:   
                result = "Q result greater"    
        return False,result
        

    num_cols = len(result1[0])

    # if the results do not have the same number of columns, they are different
    if len(result2[0]) != num_cols:
        result = "column length different"
        return False,result

    # unorder each row and compare whether the denotation is the same
    # this can already find most pair of denotations that are different
    if not quick_rej(result1, result2, order_matters):
        count =0
        for res in result2:
                if res in result1:
                    count =1
        if count ==1:
            result = "Partial Match"
        else:
            result = "order or result different"
        return False,result

    # the rest of the problem is in fact more complicated than one might think
    # we want to find a permutation of column order and a permutation of row order,
    # s.t. result_1 is the same as result_2
    # we return true if we can find such column & row permutations
    # and false if we cannot
    tab1_sets_by_columns = [{row[i] for row in result1} for i in range(num_cols)]

    # on a high level, we enumerate all possible column permutations that might make result_1 == result_2
    # we decrease the size of the column permutation space by the function get_constraint_permutation
    # if one of the permutation make result_1, result_2 equivalent, then they are equivalent
    for perm in get_constraint_permutation(tab1_sets_by_columns, result2):
        if len(perm) != len(set(perm)):
            continue
        if num_cols == 1:
            result2_perm = result2
        else:
            result2_perm = [permute_tuple(element, perm) for element in result2]
        if order_matters:
            if result1 == result2_perm:
                result ="same"
                return True,result
        else:
            # in fact the first condition must hold if the second condition holds
            # but the first is way more efficient implementation-wise
            # and we use it to quickly reject impossible candidates
            if set(result1) == set(result2_perm) and multiset_eq(result1, result2_perm):
                result ="same"
                return True,result
    return False,result


def eval_exec_match_sqlite(db, db2, p_str, g_str):
    """
    return 1 if the values between prediction and gold are matching
    in the corresponding index. Currently not support multiple col_unit(pairs).
    """
    print("p_str value---",p_str)
    
    error ='None'
    result = "error"
    conn = sqlite3.connect(db2)
    conn.text_factory = lambda b: b.decode(errors = 'ignore')
    cursor = conn.cursor()
    try:
        cursor.execute(p_str)
        p_res = cursor.fetchall()
    except Exception as e:
        # import ipdb; ipdb.set_trace()
        error =error_handling(str(e))
        return False,error,result

    conn = sqlite3.connect(db)
    conn.text_factory = lambda b: b.decode(errors = 'ignore')
    cursor = conn.cursor()
    try:
        cursor.execute(g_str)
    except Exception as e:
        error =error_handling(str(e))
        return False,error,result
    q_res = cursor.fetchall()

    ##orders_matter = 'order by' in g_str.lower()
    orders_matter = False
    value,result = result_eq(p_res, q_res, order_matters=orders_matter)
    return value,error,result

def replace_cur_year(query: str) -> str:
    return re.sub(
        "YEAR\s*\(\s*CURDATE\s*\(\s*\)\s*\)\s*", "2020", query, flags=re.IGNORECASE
    )


def eval_exec_match_db2(db2_conn,db2_conn1, p_str, g_str):
    import ibm_db
    """
    return 1 if the values between prediction and gold are matching
    in the corresponding index. Currently not support multiple col_unit(pairs).
    """
    error ='None'
    result = "error"
    try:
        stmt = ibm_db.exec_immediate(db2_conn, p_str)
        p_res = ibm_db.fetch_assoc(stmt)
    except Exception as e:
        error =error_handling(str(e))
        return False, error,result
    try:
        stmt = ibm_db.exec_immediate(db2_conn1, g_str)
        q_res = ibm_db.fetch_assoc(stmt)
    except Exception as e:
        print("error ----",e)
        error =error_handling(str(e))
        return False,error,result
    
    ##orders_matter = 'order by' in g_str.lower()
    orders_matter = False
    if q_res != 'None':
        value,result = result_eq_db2(p_res, q_res, order_matters=orders_matter)
    return value,error,result

def query_processing(row):
    g_str =''
    p_str=''
    if ';' not in row["query"]:
        g_str = row["query"]+" ;"
    else:
        g_str = row["query"]
    
    if ';' not in row["model_op"]:
        p_str = row["model_op"]+" ;"
    else:
        p_str = row["model_op"].split(";")[0]
        
    p_str = p_str.replace("> =", ">=").replace("< =", "<=").replace("! =", "!=")
    
    g_str = g_str.replace('``` ',"").replace('`',"")
    p_str = p_str.replace('``` ',"").replace('`',"")
    p_str = p_str.replace('### Expected Output:   ',"").replace('`',"")
    p_str = p_str.replace('Note:',"")
    p_str = p_str.replace(' Ex',"")
    p_str = p_str.replace('Here is the',"")
    p_str = p_str.split("### Explanation:")[0]
    p_str = p_str.split("Explanation: ")[0]
    p_str = p_str.split(": Explanation:")[0]
    p_str = p_str.split("Explanation:")[0]
    
    p_str = p_str.replace('ILIKE',"LIKE")
    p_str = p_str.replace('ilike',"LIKE")
    
    if "### Response:" in p_str:
        p_str = p_str.split("### Response:")[1]
    p_str = p_str.replace("###","")
    
    
   
    p_str_val = p_str.split(": Answer:")
    if len(p_str_val) ==2:
        p_str = p_str_val[1]
    p_str_val = p_str.split(": Query:")
    if len(p_str_val) ==2:
        p_str = p_str_val[1]
    
    if "This query" in p_str:
         p_str = p_str.split("This query")[0]
    if "The query" in p_str:
         p_str = p_str.split("The query")[0]     
    if "The above query" in p_str:
         p_str = p_str.split("The above query")[0]
    if "planation:" in p_str:
         p_str = p_str.split("planation:")[0]
    if "This queries" in p_str:
         p_str = p_str.split("This queries")[0]
    if "noqa: E501" in p_str:
         p_str = p_str.split("noqa: E501")[0]
   


    p_str = p_str.split(": Result:")[0]
    p_str = p_str.split("INST ")[0]
    p_str = p_str.split(" INST")[0]
    p_str = p_str.split(" find ")[0]
    p_str = p_str.split(" INST)")[0]
    
    
    p_str = p_str.strip()
    g_str = g_str.strip()
    p_str = p_str.replace("#","")
    p_str = reformat_query(p_str)
    p_str = replace_cur_year(p_str)
    
    if "select" in p_str.lower():
        if ':' in p_str:
            p_str=p_str.replace(":","")
        if ';' not in p_str:
            p_str=p_str+' ;'
    return g_str, p_str

def formaterAndCaller_sqlite(row,database_folder):
    db = database_folder+row["db_id"]+"/"+row["db_id"]+".sqlite"
    g_str = row["query"]+";"
    p_str =row["model_op"]
    
    ## For query correction:
    g_str_p1,p_str_p1 =query_processing(row)
    
    eval_score,e,r = eval_exec_match_sqlite(db,db,p_str, g_str)
    eval_score1 ,error,result = eval_exec_match_sqlite(db,db,p_str_p1, g_str_p1)
   
    return eval_score,eval_score1,error,result
  
def formaterAndCaller_db2(df,row):
    conn = db2_connector.db2_connectorWithSchema(row["db_id"])

    g_str = row["query"]+";"
    p_str =row["model_op"]

    eval_score1 =0
    eval_score,error,result = eval_exec_match_db2(conn,conn,p_str, g_str)
    ## For query correction:
    if "model_op1" in df.columns:
        p_str_p =row["model_op1"]
        eval_score1 ,error,result = eval_exec_match_db2(conn,conn,p_str_p, g_str)

    return eval_score,eval_score1,error,result
    

    
def ex_evalution(dbType='sqlite',exp_name='exp_codellama-13b_spider_0412',input_dataset='output/inference/exp_codellama-13b_spider_0412.csv',database_folder='input/KaggleDBQA/database/'):
    print("Component running-----------")
    config_filePath="./../expertConfig.ini"
    expertConfig = configparser.ConfigParser()
    expertConfig.read(config_filePath)
    expertConfig.sections()

    super_config = configparser.ConfigParser()
    super_config.read('./../simpleConfig.ini')
    home_dir  = super_config['Default']['home_dir']


    logging_path = home_dir+expertConfig['logs']['log_folder']+"/"+ exp_name +"_EX"
    logging.basicConfig(filename=logging_path+".log", level=logging.INFO)

    ######################################################################################################
    # Read the inference dataset
    ######################################################################################################
    df = pd.read_csv(input_dataset)
        
    if dbType =='sqlite':
        for index, row in df.iterrows():
            evalScore,value,error,result = formaterAndCaller_sqlite(row,database_folder)
            df.at[index,"evalScore"] = evalScore
            df.at[index,"evalScorePostProcessing"] = value
            df.at[index,"error_type"] = error
            df.at[index,"result"] = result
    else :
        for index, row in df.iterrows():
            evalScore,value,error,result = formaterAndCaller_db2(df,row)
            df.at[index,"evalScore"] = evalScore
            df.at[index,"evalScorePostProcessing"] = value
            df.at[index,"error_type"] = error
            df.at[index,"result"] = result
    EXAccuracy = sum(df["evalScore"])/len(df["evalScore"])
    EXAccuracyPP = sum(df["evalScorePostProcessing"])/len(df["evalScorePostProcessing"])
    logging.info("EX Accuracy :"+str(EXAccuracy))
    logging.info("PP EX Accuracy :"+str(EXAccuracyPP))
    print("PP EX Accuracy :",str(EXAccuracyPP))
    print("EX Accuracy :",str(EXAccuracy))
    df.to_csv(home_dir+"output/evalResults/"+exp_name+"_exEvaluator.csv")
    print("File saved succesfully")
