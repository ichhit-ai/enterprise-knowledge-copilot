import os
import sys

# Ensure root is in sys.path
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

# Execute the actual streamlit app from src/frontend/app.py in its correct context
app_path = os.path.join(root_dir, "src", "frontend", "app.py")
with open(app_path, "r", encoding="utf-8") as f:
    code = f.read()

# Define the execution namespace with proper __file__ referencing frontend/app.py
global_ns = {
    "__file__": app_path,
    "__name__": "__main__",
}
exec(code, global_ns)
