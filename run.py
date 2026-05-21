"""Quick validation script for Evo."""

from evo.main import main

if __name__ == "__main__":
    import sys
    sys.argv = ["evo", "run", "--task", "test task"]
    main()
