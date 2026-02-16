import json

import file_path

encoding='utf-8'

with open(file_path.种族, "r", encoding=encoding) as f:
	zhongsheng_animal_races = json.loads(f.read())
with open(file_path.种族信息, "r", encoding=encoding) as f:
	animal_stats = json.loads(f.read())