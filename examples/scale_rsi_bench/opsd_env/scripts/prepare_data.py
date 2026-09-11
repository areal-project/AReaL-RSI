"""Build the dataset cache expected by the unchanged OPSD training entrypoint."""
from datasets import load_dataset
ds = load_dataset("siyanzhao/Openthoughts_math_30k_opsd", revision="1f33e9dc2e8a1c639ca74f8024ad4a9f1f5eae62")
print(ds)
print("OPSD DATA READY")
