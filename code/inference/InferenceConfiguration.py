from configparser import ExtendedInterpolation
import configparser
import os

class InferenceConfiguration():
     
     def __init__(self, expertConfig) -> None:
         self.super_config = expertConfig
     
     def build(self) -> dict:
        
        config_filePath="./../expertConfig.ini"
        expertConfig = configparser.ConfigParser(interpolation=ExtendedInterpolation())
        expertConfig.read(config_filePath)

        ## Home directory
        home_dir  = self.super_config['Default']['home_dir']
        exp_name  = self.super_config['Default']['exp_name']

        inference_type = self.super_config["Inference"]["inference_type"]

        # input_dataset = home_dir+input_dataset
        input_dataset = home_dir + self.super_config['Inference']['input_dataset']

        # Base model identifed
        base_model = self.super_config['Inference']['model_name']

        # Looking for finetuned model
        finetuned_model = self.super_config['Inference']['finetuned_model']
        if(os.path.exists(finetuned_model) is False):
            finetuned_model = None

        logging_path = f"{home_dir}{expertConfig['logs']['log_folder']}{exp_name}_infer"
        output_location = f"{home_dir}output/inference/{exp_name}_inference.csv"

        ## Watsonx details
        watsonx_url = expertConfig["Inference"]["watsonx_url"]
        watsonx_apikey =  expertConfig["Inference"]["watsonx_apikey"]
        watsonx_projectId = expertConfig["Inference"]["watsonx_projectID"]

        return {
            "base_model": base_model,
            "input_dataset": input_dataset,
            "logging_path": logging_path,
            "finetuned_model": finetuned_model,
            "output_location": output_location,
            "watsonx_url": watsonx_url,
            "watsonx_apikey": watsonx_apikey,
            "watsonx_projectId": watsonx_projectId,
            "inference_type": inference_type,
            "exp_name": exp_name
        }

