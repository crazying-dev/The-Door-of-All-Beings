from typing import Any


def Selection_Dialog(title:str, text:str, choices_button:dict[str, tuple[object, list[Any]]]={"确定":(exit, [0])}):
	"""
	:param title: 窗口标题
	:param text: 窗口内中央文字
	:param choices_button: 字典[按钮文字, 元组[执行函数(特殊内容:return, 以元组形式返回参数内容), 列表[参数]]]
	:return: None | 其他
	"""
	...