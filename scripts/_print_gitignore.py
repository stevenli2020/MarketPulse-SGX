"""One-off diagnostic: print .gitignore contents before untracking anything."""
with open(".gitignore") as f:
    print(f.read())
