from __future__ import absolute_import

import imp
import shutil
from pathlib import Path

import numpy
import pytest
import rasterio

from datacube.compat import string_types
from integration_tests.utils import assert_click_command
from integration_tests.conftest import prepare_test_ingestion_configuration

PROJECT_ROOT = Path(__file__).parents[1]
CONFIG_SAMPLES = PROJECT_ROOT / 'docs/config_samples/'
LS5_DATASET_TYPES = CONFIG_SAMPLES / 'dataset_types/ls5_scenes.yaml'
TEST_DATA = PROJECT_ROOT / 'tests' / 'data' / 'lbg'

INGESTER_CONFIGS = CONFIG_SAMPLES / 'ingester'

LS5_NBAR_ALBERS = 'ls5_nbar_albers.yaml'
LS5_PQ_ALBERS = 'ls5_pq_albers.yaml'

GA_LS_PREPARE_SCRIPT = PROJECT_ROOT / 'utils/galsprepare.py'

galsprepare = imp.load_source('module.name', str(GA_LS_PREPARE_SCRIPT))

LBG_NBAR = 'LS5_TM_NBAR_P54_GANBAR01-002_090_084_19920323'
LBG_PQ = 'LS5_TM_PQ_P55_GAPQ01-002_090_084_19920323'

ALBERS_ELEMENT_SIZE = 25

LBG_CELL_X = 15
LBG_CELL_Y = -40
LBG_CELL = (LBG_CELL_X, LBG_CELL_Y)


@pytest.fixture()
def testdata_dir(tmpdir, ingest_configs):
    datadir = Path(str(tmpdir), 'data')
    datadir.mkdir()

    shutil.copytree(str(TEST_DATA), str(tmpdir / 'lbg'))

    for file in ingest_configs.values():
        prepare_test_ingestion_configuration(tmpdir, tmpdir, INGESTER_CONFIGS/file,
                                             mode='end2end')

    return tmpdir


ignore_me = pytest.mark.xfail(True, reason="get_data/get_description still to be fixed in Unification")


@pytest.mark.usefixtures('default_metadata_type')
@pytest.mark.parametrize('datacube_env_name', ('datacube', 's3aio_env', ), indirect=True)
def test_end_to_end(clirunner, index, testdata_dir, ingest_configs):
    """
    Loads two dataset configurations, then ingests a sample Landsat 5 scene

    One dataset configuration specifies Australian Albers Equal Area Projection,
    the other is simply latitude/longitude.

    The input dataset should be recorded in the index, and two sets of storage units
    should be created on disk and recorded in the index.
    """

    lbg_nbar = testdata_dir / 'lbg' / LBG_NBAR
    lbg_pq = testdata_dir / 'lbg' / LBG_PQ
    ls5_nbar_albers_ingest_config = testdata_dir / ingest_configs['ls5_nbar_albers']
    ls5_pq_albers_ingest_config = testdata_dir / ingest_configs['ls5_pq_albers']

    # Run galsprepare.py on the NBAR and PQ scenes
    assert_click_command(galsprepare.main, [str(lbg_nbar)])

    # Add the LS5 Dataset Types
    clirunner(['-v', 'product', 'add', str(LS5_DATASET_TYPES)])

    # Index the Datasets
    #  - do test run first to increase test coverage
    clirunner(['-v', 'dataset', 'add', '--dry-run',
               str(lbg_nbar), str(lbg_pq)])

    #  - do actual indexing
    clirunner(['-v', 'dataset', 'add',
               str(lbg_nbar), str(lbg_pq)])

    # Test no-op update
    for policy in ['archive', 'forget', 'keep']:
        clirunner(['-v', 'dataset', 'update',
                   '--dry-run',
                   '--location-policy', policy,
                   str(lbg_nbar), str(lbg_pq)])

        # Test no changes needed update
        clirunner(['-v', 'dataset', 'update',
                   '--location-policy', policy,
                   str(lbg_nbar), str(lbg_pq)])

    # TODO: test location update
    # 1. Make a copy of a file
    # 2. Call dataset update with archive/forget
    # 3. Check location

    # Ingest NBAR
    clirunner(['-v', 'ingest', '-c', str(ls5_nbar_albers_ingest_config)])

    # Ingest PQ
    clirunner(['-v', 'ingest', '-c', str(ls5_pq_albers_ingest_config)])

    check_open_with_dc(index)
    check_open_with_grid_workflow(index)


def check_open_with_dc(index):
    from datacube.api.core import Datacube
    dc = Datacube(index=index)

    data_array = dc.load(product='ls5_nbar_albers', measurements=['blue']).to_array(dim='variable')
    assert data_array.shape
    assert (data_array != -999).any()

    data_array = dc.load(product='ls5_nbar_albers', measurements=['blue'], time='1992-03-23T23:14:25.500000')
    assert data_array['blue'].shape[0] == 1
    assert (data_array.blue != -999).any()

    data_array = dc.load(product='ls5_nbar_albers', measurements=['blue'], latitude=-35.3, longitude=149.1)
    assert data_array['blue'].shape[1:] == (1, 1)
    assert (data_array.blue != -999).any()

    data_array = dc.load(product='ls5_nbar_albers', latitude=(-35, -36), longitude=(149, 150)).to_array(dim='variable')

    assert data_array.ndim == 4
    assert 'variable' in data_array.dims
    assert (data_array != -999).any()

    with rasterio.Env():
        lazy_data_array = dc.load(product='ls5_nbar_albers', latitude=(-35, -36), longitude=(149, 150),
                                  dask_chunks={'time': 1, 'x': 1000, 'y': 1000}).to_array(dim='variable')
        assert lazy_data_array.data.dask
        assert lazy_data_array.ndim == data_array.ndim
        assert 'variable' in lazy_data_array.dims
        assert lazy_data_array[1, :2, 950:1050, 950:1050].equals(data_array[1, :2, 950:1050, 950:1050])

    dataset = dc.load(product='ls5_nbar_albers', measurements=['blue'])
    assert dataset['blue'].size

    dataset = dc.load(product='ls5_nbar_albers', latitude=(-35.2, -35.3), longitude=(149.1, 149.2))
    assert dataset['blue'].size

    with rasterio.Env():
        lazy_dataset = dc.load(product='ls5_nbar_albers', latitude=(-35.2, -35.3), longitude=(149.1, 149.2),
                               dask_chunks={'time': 1})
        assert lazy_dataset['blue'].data.dask
        assert lazy_dataset.blue[:2, :100, :100].equals(dataset.blue[:2, :100, :100])
        assert lazy_dataset.isel(time=slice(0, 2), x=slice(950, 1050), y=slice(950, 1050)).equals(
            dataset.isel(time=slice(0, 2), x=slice(950, 1050), y=slice(950, 1050)))

    dataset_like = dc.load(product='ls5_nbar_albers', measurements=['blue'], like=dataset)
    assert (dataset.blue == dataset_like.blue).all()

    solar_day_dataset = dc.load(product='ls5_nbar_albers',
                                latitude=(-35, -36), longitude=(149, 150),
                                measurements=['blue'], group_by='solar_day')
    assert 0 < solar_day_dataset.time.size <= dataset.time.size

    dataset = dc.load(product='ls5_nbar_albers', latitude=(-35.2, -35.3), longitude=(149.1, 149.2), align=(5, 20))
    assert dataset.geobox.affine.f % abs(dataset.geobox.affine.e) == 5
    assert dataset.geobox.affine.c % abs(dataset.geobox.affine.a) == 20
    dataset_like = dc.load(product='ls5_nbar_albers', measurements=['blue'], like=dataset)
    assert (dataset.blue == dataset_like.blue).all()

    products_df = dc.list_products()
    assert len(products_df)
    assert len(products_df[products_df['name'].isin(['ls5_nbar_albers'])])
    assert len(products_df[products_df['name'].isin(['ls5_pq_albers'])])

    assert len(dc.list_measurements())

    resamp = ['nearest', 'cubic', 'bilinear', 'cubic_spline', 'lanczos', 'average']
    results = {}

    # WTF
    def calc_max_change(da):
        midline = int(da.shape[0] * 0.5)
        a = int(abs(da[midline, :-1].data - da[midline, 1:].data).max())

        centerline = int(da.shape[1] * 0.5)
        b = int(abs(da[:-1, centerline].data - da[1:, centerline].data).max())
        return a + b

    for resamp_meth in resamp:
        dataset = dc.load(product='ls5_nbar_albers', measurements=['blue'],
                          latitude=(-35.28, -35.285), longitude=(149.15, 149.155),
                          output_crs='EPSG:4326', resolution=(-0.0000125, 0.0000125), resampling=resamp_meth)
        results[resamp_meth] = calc_max_change(dataset.blue.isel(time=0))

    assert results['cubic_spline'] < results['nearest']
    assert results['lanczos'] < results['average']


def check_open_with_grid_workflow(index):
    type_name = 'ls5_nbar_albers'
    dt = index.products.get_by_name(type_name)

    from datacube.api.grid_workflow import GridWorkflow
    gw = GridWorkflow(index, dt.grid_spec)

    cells = gw.list_cells(product=type_name, cell_index=LBG_CELL)
    assert LBG_CELL in cells

    cells = gw.list_cells(product=type_name)
    assert LBG_CELL in cells

    tile = cells[LBG_CELL]
    assert 'x' in tile.dims
    assert 'y' in tile.dims
    assert 'time' in tile.dims
    assert tile.shape[1] == 4000
    assert tile.shape[2] == 4000
    assert tile[:1, :100, :100].shape == (1, 100, 100)
    dataset_cell = gw.load(tile, measurements=['blue'])
    assert dataset_cell['blue'].shape == tile.shape

    for timestamp, tile_slice in tile.split('time'):
        assert tile_slice.shape == (1, 4000, 4000)

    dataset_cell = gw.load(tile)
    assert all(m in dataset_cell for m in ['blue', 'green', 'red', 'nir', 'swir1', 'swir2'])

    ts = numpy.datetime64('1992-03-23T23:14:25.500000000')
    tile_key = LBG_CELL + (ts,)
    tiles = gw.list_tiles(product=type_name)
    assert tiles
    assert tile_key in tiles

    tile = tiles[tile_key]
    dataset_cell = gw.load(tile, measurements=['blue'])
    assert dataset_cell['blue'].size

    dataset_cell = gw.load(tile)
    assert all(m in dataset_cell for m in ['blue', 'green', 'red', 'nir', 'swir1', 'swir2'])
