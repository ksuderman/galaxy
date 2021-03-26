import os

from galaxy.util import galaxy_root_path
from .framework import (
    selenium_test,
    SeleniumTestCase,
    TIMEOUT_MULTIPLIER,
)

STOCK_TOURS_DIRECTORY = os.path.join(galaxy_root_path, "config", "plugins", "tours")


class TestStockToursTestCase(SeleniumTestCase):

    # @selenium_test
    # def test_core_galaxy_ui(self):
    #     sleep_on_steps = {
    #         "Tools": 20 * TIMEOUT_MULTIPLIER,
    #         "Select a tool": 2 * TIMEOUT_MULTIPLIER,
    #         "History": 20 * TIMEOUT_MULTIPLIER,
    #     }
    #     self.run_tour(
    #         os.path.join(STOCK_TOURS_DIRECTORY, "core.galaxy_ui.yaml"),
    #         sleep_on_steps=sleep_on_steps
    #     )


    # @selenium_test
    # def test_assemby_debruijn_graph(self):
    #     self.run_tour(
    #         os.path.join(STOCK_TOURS_DIRECTORY, "assembly.debruijn_graph.yaml"),
    #     )

    def tour(self, tour):
        self.run_tour(os.path.join(STOCK_TOURS_DIRECTORY, tour))

    # @selenium_test
    # def test_assembly_general_introduction(self):
    #     self.run("assembly.general_introduction.yaml")

    @selenium_test
    def test_unicycler_assembly(self):
        self.tour('unicycler-assembly.yaml')

    @selenium_test
    def test_plasmid_metagenomics_nanopore(self):
        self.tour('plasmid-metagenomics-nanopore.yaml')

    @selenium_test
    def test_iwtomics(self):
        self.tour('iwtomics.yaml')

