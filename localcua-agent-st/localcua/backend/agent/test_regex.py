import re

# old regex: r'(\w+)=(\([^)]*\)|[^,]+)'
_KWARG_RE = re.compile(r'(\w+)=([\'"]?\([^)]*\)[\'"]?|[^,]+)')

action_str1 = "start_box='(490,1060)', text='hello'"
action_str2 = "start_box=(490,1060), amount=3"

print("Regex findall result 1:")
print(_KWARG_RE.findall(action_str1))
print("Regex findall result 2:")
print(_KWARG_RE.findall(action_str2))
