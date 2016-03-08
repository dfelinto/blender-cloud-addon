
import sys
import os

# Add our shipped Pillar SDK wheel to the Python path
if not any('pillar_sdk' in path for path in sys.path):
    import glob
    # TODO: gracefully handle errors when the wheel cannot be found.
    my_dir = os.path.dirname(__file__)
    pillar_wheel = glob.glob(os.path.join(my_dir, 'pillar_sdk*.whl'))[0]
    sys.path.append(pillar_wheel)

import pillarsdk

