"""Unittests for blender_cloud.utils."""

import pathlib
import unittest

from blender_cloud import utils


class FindInPathTest(unittest.TestCase):
    def test_nonexistant_path(self):
        path = pathlib.Path('/doesnotexistreally')
        self.assertFalse(path.exists())
        self.assertIsNone(utils.find_in_path(path, 'jemoeder.blend'))

    def test_really_breadth_first(self):
        """A depth-first test might find dir_a1/dir_a2/dir_a3/find_me.txt first."""

        path = pathlib.Path(__file__).parent / 'test_really_breadth_first'
        found = utils.find_in_path(path, 'find_me.txt')
        self.assertEqual(path / 'dir_b1' / 'dir_b2' / 'find_me.txt', found)

    def test_nonexistant_file(self):
        path = pathlib.Path(__file__).parent / 'test_really_breadth_first'
        found = utils.find_in_path(path, 'do_not_find_me.txt')
        self.assertEqual(None, found)
