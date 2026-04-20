# -*- coding: utf-8 -*-
"""pyRevit launcher shim for FlatSlabRebar bundle.

This outer-level script exists so pyRevit can find a target `script.py`
directly inside `FlatSlabRebar.pushbutton` after the repository was
restructured to include nested button folders.
"""
from __future__ import print_function

import os
import sys
import imp


def _load_inner_module():
    outer_dir = os.path.dirname(__file__)
    inner_dir = os.path.join(outer_dir, 'FlatSlabRebar.pushbutton')
    inner_script = os.path.join(inner_dir, 'script.py')

    if not os.path.exists(inner_script):
        raise IOError('Inner script not found: {}'.format(inner_script))

    if inner_dir not in sys.path:
        sys.path.insert(0, inner_dir)

    return imp.load_source('flatslabrft_inner_script', inner_script)


def main():
    inner = _load_inner_module()
    if not hasattr(inner, 'main'):
        raise AttributeError('Inner module does not expose main()')
    return inner.main()


if __name__ == '__main__':
    main()
