import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cps.app import main

if len(sys.argv) == 3 and sys.argv[1] == "--verify-release":
    from cps.releasecheck import verify
    sys.exit(verify(sys.argv[2]))

main()
