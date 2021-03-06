import ntplib

import global_test_setup

import copy
import json
import logging
import os
import unittest

import mock
import numpy as np
import xarray as xr
from ion_functions.data.ctd_functions import ctd_sbe16plus_tempwat, ctd_pracsal

from preload_database.database import create_engine_from_url, create_scoped_session
from ooi_data.postgres.model import Parameter, MetadataBase
from util.advlogging import jdefault
from util.annotation import AnnotationRecord, AnnotationStore
from util.asset_management import AssetEvents
from util.common import StreamKey
from util.stream_dataset import StreamDataset

TEST_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(TEST_DIR, 'data')

engine = create_engine_from_url(None)
session = create_scoped_session(engine)
MetadataBase.query = session.query_property()

logging.basicConfig()
log = logging.getLogger()
log.setLevel(logging.DEBUG)


class StreamDatasetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nutnr_sk = StreamKey('CE04OSPS', 'SF01B', '4A-NUTNRA102', 'streamed', 'nutnr_a_sample')
        cls.ctdpf_sk = StreamKey('CE04OSPS', 'SF01B', '2A-CTDPFA107', 'streamed', 'ctdpf_sbe43_sample')
        cls.metbk_sk = StreamKey('CE02SHSM', 'SBD11', '06-METBKA000', 'telemetered', 'metbk_a_dcl_instrument')
        cls.velpt_sk = StreamKey('CE02SHSM', 'SBD11', '04-VELPTA000', 'telemetered', 'velpt_ab_dcl_instrument')
        cls.nutnr_fn = 'nutnr_a_sample.nc'
        cls.ctdpf_fn = 'ctdpf_sbe43_sample.nc'
        cls.metbk_fn = 'metbk_a_dcl_instrument.nc'
        cls.velpt_fn = 'velpt_ab_dcl_instrument.nc'
        cls.ctd_events = AssetEvents(cls.ctdpf_sk.as_three_part_refdes(),
                                     json.load(open(os.path.join(DATA_DIR, 'CE04OSPS-SF01B-2A-CTDPFA107_events.json'))))
        cls.nut_events = AssetEvents(cls.nutnr_sk.as_three_part_refdes(),
                                     json.load(open(os.path.join(DATA_DIR, 'CE04OSPS-SF01B-4A-NUTNRA102_events.json'))))
        cls.velpt_events = AssetEvents(cls.velpt_sk.as_three_part_refdes(),
                                       json.load(open(os.path.join(DATA_DIR,
                                                                   'CE02SHSM-SBD11-04-VELPTA000_events.json'))))
        cls.metbk_events = AssetEvents(cls.metbk_sk.as_three_part_refdes(),
                                       json.load(open(os.path.join(DATA_DIR,
                                                                   'CE02SHSM-SBD11-06-METBKA000_events.json'))))
        # AnnotationStore will only add one AnnotationRecord with a given id - use this to increment id
        cls.annotation_id_counter = 0

    # make sure the datasets were added and are not fill values
    def assert_parameters_in_datasets(self, datasets, parameters, expect_fill=False):
        for dataset in datasets.itervalues():
            for parameter in parameters:
                self.assertIn(parameter, dataset)
                values = dataset[parameter].values
                filled = (values == dataset[parameter]._FillValue).all()
                if filled and not expect_fill:
                    self.assertFalse(True, msg='parameter (%s) not computed - all fill values' % parameter)
                if not filled and expect_fill:
                    self.assertFalse(True, msg='parameter (%s) not filled (as expected)' % parameter)

    def _create_exclusion_anno(self, streamkey, start, stop):
        # increment id
        self.annotation_id_counter += 1
        key = streamkey.as_dict()
        return AnnotationRecord(id=self.annotation_id_counter, beginDT=start, endDT=stop, subsite=key['subsite'],
                                node=key['node'], sensor=key['sensor'], method=key['method'], stream=key['stream'],
                                exclusionFlag=True)

    def test_calculate_internal_single_deployment(self):
        ctd_ds = xr.open_dataset(os.path.join(DATA_DIR, self.ctdpf_fn), decode_times=False)
        ctd_ds = ctd_ds[['obs', 'time', 'deployment', 'temperature', 'pressure',
                         'pressure_temp', 'conductivity', 'ext_volt0']]

        ctd_stream_dataset = StreamDataset(self.ctdpf_sk, {}, [], 'UNIT')
        ctd_stream_dataset.events = self.ctd_events
        ctd_stream_dataset._insert_dataset(ctd_ds)
        ctd_stream_dataset.calculate_all()

        for deployment in ctd_stream_dataset.datasets:
            ds = ctd_stream_dataset.datasets[deployment]
            tempwat = ctd_sbe16plus_tempwat(ds.temperature,
                                            ctd_stream_dataset.events.get_cal('CC_a0', deployment)[0][2],
                                            ctd_stream_dataset.events.get_cal('CC_a1', deployment)[0][2],
                                            ctd_stream_dataset.events.get_cal('CC_a2', deployment)[0][2],
                                            ctd_stream_dataset.events.get_cal('CC_a3', deployment)[0][2])
            np.testing.assert_array_equal(ds.seawater_temperature, tempwat)

            pracsal = ctd_pracsal(ds.seawater_conductivity,
                                  ds.seawater_temperature,
                                  ds.seawater_pressure)
            np.testing.assert_array_equal(ds.practical_salinity, pracsal)

    def test_calculate_internal_multiple_deployments(self):
        ctd_ds = xr.open_dataset(os.path.join(DATA_DIR, self.ctdpf_fn), decode_times=False)
        ctd_ds = ctd_ds[['obs', 'time', 'deployment', 'temperature', 'pressure',
                         'pressure_temp', 'conductivity', 'ext_volt0']]

        # remap times to make this two separate deployments
        dep1_start = self.ctd_events.deps[1].ntp_start
        dep2_stop = self.ctd_events.deps[2].ntp_start + 864000
        ctd_ds.time.values = np.linspace(dep1_start+1, dep2_stop-1, num=ctd_ds.time.shape[0])

        ctd_stream_dataset = StreamDataset(self.ctdpf_sk, {}, [], 'UNIT')
        ctd_stream_dataset.events = self.ctd_events
        ctd_stream_dataset._insert_dataset(ctd_ds)
        ctd_stream_dataset.calculate_all()

        for deployment in ctd_stream_dataset.datasets:
            ds = ctd_stream_dataset.datasets[deployment]
            tempwat = ctd_sbe16plus_tempwat(ds.temperature,
                                            ctd_stream_dataset.events.get_cal('CC_a0', deployment)[0][2],
                                            ctd_stream_dataset.events.get_cal('CC_a1', deployment)[0][2],
                                            ctd_stream_dataset.events.get_cal('CC_a2', deployment)[0][2],
                                            ctd_stream_dataset.events.get_cal('CC_a3', deployment)[0][2])
            np.testing.assert_array_equal(ds.seawater_temperature, tempwat)

            pracsal = ctd_pracsal(ds.seawater_conductivity,
                                  ds.seawater_temperature,
                                  ds.seawater_pressure)
            np.testing.assert_array_equal(ds.practical_salinity, pracsal)

    def test_calculate_external_single_deployment(self):
        ctd_ds = xr.open_dataset(os.path.join(DATA_DIR, self.ctdpf_fn), decode_times=False)
        nut_ds = xr.open_dataset(os.path.join(DATA_DIR, self.nutnr_fn), decode_times=False)

        ctd_ds = ctd_ds[['obs', 'time', 'deployment', 'temperature', 'pressure',
                         'pressure_temp', 'conductivity', 'ext_volt0']]
        nut_ds = nut_ds[['obs', 'time', 'deployment', 'spectral_channels',
                         'frame_type', 'nutnr_dark_value_used_for_fit']]

        ctd_stream_dataset = StreamDataset(self.ctdpf_sk, {}, [], 'UNIT')
        ctd_stream_dataset.events = self.ctd_events
        ctd_stream_dataset._insert_dataset(ctd_ds)
        ctd_stream_dataset.calculate_all()

        nut_stream_dataset = StreamDataset(self.nutnr_sk, {}, [self.ctdpf_sk], 'UNIT')
        nut_stream_dataset.events = self.nut_events
        nut_stream_dataset._insert_dataset(nut_ds)
        nut_stream_dataset.calculate_all()

        nut_stream_dataset.interpolate_needed({self.ctdpf_sk: ctd_stream_dataset})
        nut_stream_dataset.calculate_all()

        expected_params = ['ctdpf_sbe43_sample-seawater_temperature',
                           'ctdpf_sbe43_sample-practical_salinity',
                           'salinity_corrected_nitrate']
        self.assert_parameters_in_datasets(nut_stream_dataset.datasets, expected_params)

    def test_fill_missing(self):
        velpt_ds = xr.open_dataset(os.path.join(DATA_DIR, self.velpt_fn), decode_times=False)

        velpt_ds = velpt_ds[['obs', 'time', 'deployment', 'velocity_beam1', 'velocity_beam2', 'velocity_beam3',
                             'amplitude_beam1', 'amplitude_beam2', 'amplitude_beam3']]

        velpt_stream_dataset = StreamDataset(self.velpt_sk, {}, [], 'UNIT')
        velpt_stream_dataset.events = self.velpt_events
        velpt_stream_dataset._insert_dataset(velpt_ds)
        velpt_stream_dataset.fill_missing()

        expected_params = ['eastward_velocity']

        self.assert_parameters_in_datasets(velpt_stream_dataset.datasets, expected_params, expect_fill=True)

    def test_calculate_external_12035(self):
        velpt_ds = xr.open_dataset(os.path.join(DATA_DIR, self.velpt_fn), decode_times=False)
        metbk_ds = xr.open_dataset(os.path.join(DATA_DIR, self.metbk_fn), decode_times=False)

        velpt_ds = velpt_ds[['obs', 'time', 'deployment', 'velocity_beam1', 'velocity_beam2', 'velocity_beam3',
                             'amplitude_beam1', 'amplitude_beam2', 'amplitude_beam3']]

        metbk_ds = metbk_ds[['obs', 'time', 'deployment', 'barometric_pressure', 'relative_humidity',
                             'air_temperature', 'longwave_irradiance', 'precipitation', 'sea_surface_temperature',
                             'sea_surface_conductivity', 'shortwave_irradiance', 'eastward_wind_velocity',
                             'northward_wind_velocity']]

        velpt_stream_dataset = StreamDataset(self.velpt_sk, {}, [], 'UNIT')
        velpt_stream_dataset.events = self.velpt_events
        velpt_stream_dataset._insert_dataset(velpt_ds)
        velpt_stream_dataset.calculate_all()

        metbk_stream_dataset = StreamDataset(self.metbk_sk, {}, [self.velpt_sk], 'UNIT')
        metbk_stream_dataset.events = self.metbk_events
        metbk_stream_dataset._insert_dataset(metbk_ds)
        metbk_stream_dataset.calculate_all()
        metbk_stream_dataset.interpolate_needed({self.velpt_sk: velpt_stream_dataset})
        metbk_stream_dataset.calculate_all()

        expected_params = ['met_barpres',
                           'met_windavg_mag_corr_east',
                           'met_windavg_mag_corr_north',
                           'met_current_direction',
                           'met_current_speed',
                           'met_relwind_direction',
                           'met_relwind_speed',
                           'met_netsirr',
                           'met_salsurf',
                           'met_spechum',
                           'met_heatflx_minute',
                           'met_latnflx_minute',
                           'met_netlirr_minute',
                           'met_sensflx_minute',
                           ]
        self.assert_parameters_in_datasets(metbk_stream_dataset.datasets, expected_params)

    def test_calculate_external_multiple_deployments(self):
        ctd_ds = xr.open_dataset(os.path.join(DATA_DIR, self.ctdpf_fn), decode_times=False)
        nut_ds = xr.open_dataset(os.path.join(DATA_DIR, self.nutnr_fn), decode_times=False)

        ctd_ds = ctd_ds[['obs', 'time', 'deployment', 'temperature', 'pressure',
                         'pressure_temp', 'conductivity', 'ext_volt0']]
        nut_ds = nut_ds[['obs', 'time', 'deployment', 'spectral_channels',
                         'frame_type', 'nutnr_dark_value_used_for_fit']]

        # remap times to make this two separate deployments
        dep1_start = self.ctd_events.deps[1].ntp_start
        dep2_stop = self.ctd_events.deps[2].ntp_start + 864000
        ctd_ds.time.values = np.linspace(dep1_start + 1, dep2_stop - 1, num=ctd_ds.time.shape[0])
        nut_ds.time.values = np.linspace(dep1_start + 1, dep2_stop - 1, num=nut_ds.time.shape[0])

        ctd_stream_dataset = StreamDataset(self.ctdpf_sk, {}, [], 'UNIT')
        ctd_stream_dataset.events = self.ctd_events
        ctd_stream_dataset._insert_dataset(ctd_ds)
        ctd_stream_dataset.calculate_all()

        nut_stream_dataset = StreamDataset(self.nutnr_sk, {}, [self.ctdpf_sk], 'UNIT')
        nut_stream_dataset.events = self.nut_events
        nut_stream_dataset._insert_dataset(nut_ds)
        nut_stream_dataset.calculate_all()

        nut_stream_dataset.interpolate_needed({self.ctdpf_sk: ctd_stream_dataset})
        nut_stream_dataset.calculate_all()

        expected_params = ['ctdpf_sbe43_sample-seawater_temperature',
                           'ctdpf_sbe43_sample-practical_salinity',
                           'salinity_corrected_nitrate']
        self.assert_parameters_in_datasets(nut_stream_dataset.datasets, expected_params)

    def test_log_algorithm_inputs(self):
        def mock_write(self):
            return json.dumps(self.m_qdata, default=jdefault)

        uflags = {'advancedStreamEngineLogging': True, 'userName': 'test'}
        ctd_ds = xr.open_dataset(os.path.join(DATA_DIR, self.ctdpf_fn), decode_times=False)
        ctd_ds = ctd_ds[['obs', 'time', 'deployment', 'temperature', 'pressure',
                         'pressure_temp', 'conductivity', 'ext_volt0']]

        ctd_stream_dataset = StreamDataset(self.ctdpf_sk, uflags, [], 'UNIT')
        ctd_stream_dataset.events = self.ctd_events
        ctd_stream_dataset._insert_dataset(ctd_ds)

        parameter = Parameter.query.get(13)
        with mock.patch('util.stream_dataset.ParameterReport.write', new=mock_write):
            result = ctd_stream_dataset._log_algorithm_inputs(parameter, {}, np.array([1, 2, 3]), self.ctdpf_sk, ctd_ds)
            self.assertIsNotNone(result)

    def test_log_algorithm_inputs_no_result(self):
        def mock_write(self):
            return json.dumps(self.m_qdata, default=jdefault)

        uflags = {'advancedStreamEngineLogging': True, 'userName': 'test'}
        ctd_ds = xr.open_dataset(os.path.join(DATA_DIR, self.ctdpf_fn), decode_times=False)
        ctd_ds = ctd_ds[['obs', 'time', 'deployment', 'temperature', 'pressure',
                         'pressure_temp', 'conductivity', 'ext_volt0']]

        ctd_stream_dataset = StreamDataset(self.ctdpf_sk, uflags, [], 'UNIT')
        ctd_stream_dataset.events = self.ctd_events
        ctd_stream_dataset._insert_dataset(ctd_ds)

        parameter = Parameter.query.get(13)
        with mock.patch('util.stream_dataset.ParameterReport.write', new=mock_write):
            result = ctd_stream_dataset._log_algorithm_inputs(parameter, {}, None, self.ctdpf_sk, ctd_ds)
            self.assertIsNotNone(result)

    def test_exclude_data(self):
        ctd_ds = xr.open_dataset(os.path.join(DATA_DIR, self.ctdpf_fn), decode_times=False)
        ctd_ds = ctd_ds[['obs', 'time', 'deployment', 'temperature', 'pressure',
                         'pressure_temp', 'conductivity', 'ext_volt0']]

        times = ctd_ds.time.values
        store = AnnotationStore()

        ctd_stream_dataset = StreamDataset(self.ctdpf_sk, {}, [], 'UNIT')
        ctd_stream_dataset.events = self.ctd_events
        ctd_stream_dataset._insert_dataset(ctd_ds)
        
        ctd_stream_dataset.exclude_flagged_data(store)
        np.testing.assert_array_equal(times, ctd_stream_dataset.datasets[2].time.values)

        # exclude a bit
        start = ntplib.ntp_to_system_time(times[0]) * 1000
        stop = ntplib.ntp_to_system_time(times[100]) * 1000
        anno = self._create_exclusion_anno(self.ctdpf_sk, start, stop)
        store.add_annotations([anno])

        ctd_stream_dataset.exclude_flagged_data(store)
        np.testing.assert_array_equal(times[101:], ctd_stream_dataset.datasets[2].time.values)

        # exclude everything
        start = ntplib.ntp_to_system_time(times[0]) * 1000
        stop = ntplib.ntp_to_system_time(times[-1]) * 1000
        anno = self._create_exclusion_anno(self.ctdpf_sk, start, stop)
        store.add_annotations([anno])

        ctd_stream_dataset.exclude_flagged_data(store)
        self.assertNotIn(2, ctd_stream_dataset.datasets)

    def test_insert_valid_scalar_data(self):
        ctd_ds = xr.open_dataset(os.path.join(DATA_DIR, self.ctdpf_fn), decode_times=False)
        ctd_ds = ctd_ds[['obs', 'time', 'deployment', 'temperature', 'pressure',
                         'pressure_temp', 'conductivity', 'ext_volt0']]

        data = np.zeros_like(ctd_ds.time.values)
        param = Parameter.query.get(3777)

        StreamDataset._insert_data(ctd_ds, param, data)
        self.assertIn('corrected_dissolved_oxygen', ctd_ds)

    def test_insert_fill_scalar_data(self):
        ctd_ds = xr.open_dataset(os.path.join(DATA_DIR, self.ctdpf_fn), decode_times=False)
        ctd_ds = ctd_ds[['obs', 'time', 'deployment', 'temperature', 'pressure',
                         'pressure_temp', 'conductivity', 'ext_volt0']]

        data = None
        param = Parameter.query.get(3777)

        StreamDataset._insert_data(ctd_ds, param, data)
        self.assertIn('corrected_dissolved_oxygen', ctd_ds)

        filled = np.zeros_like(ctd_ds.time.values)
        filled[:] = param.fill_value
        np.testing.assert_equal(ctd_ds.corrected_dissolved_oxygen, filled)

    def test_insert_bad_length_data(self):
        ctd_ds = xr.open_dataset(os.path.join(DATA_DIR, self.ctdpf_fn), decode_times=False)
        ctd_ds = ctd_ds[['obs', 'time', 'deployment', 'temperature', 'pressure',
                         'pressure_temp', 'conductivity', 'ext_volt0']]

        data = np.arange(0, 100)
        param = Parameter.query.get(3777)

        with self.assertRaises(ValueError):
            StreamDataset._insert_data(ctd_ds, param, data)

        self.assertNotIn('corrected_dissolved_oxygen', ctd_ds)

    def test_insert_valid_array_data(self):
        adcp_fn = 'deployment0000_RS03AXBS-LJ03A-10-ADCPTE301-streamed-adcp_velocity_beam.nc'
        adcp_ds = xr.open_dataset(os.path.join(DATA_DIR, adcp_fn), decode_times=False)

        data = np.zeros_like(adcp_ds.velocity_beam1)
        param = Parameter.query.get(2769)

        StreamDataset._insert_data(adcp_ds, param, data)
        self.assertIn('corrected_echo_intensity_beam1', adcp_ds)

        self.assertEqual(set(adcp_ds.corrected_echo_intensity_beam1.dims), {'obs', 'bin'})

    def test_insert_fill_array_data(self):
        adcp_fn = 'deployment0000_RS03AXBS-LJ03A-10-ADCPTE301-streamed-adcp_velocity_beam.nc'
        adcp_ds = xr.open_dataset(os.path.join(DATA_DIR, adcp_fn), decode_times=False)

        data = None
        param = Parameter.query.get(2769)

        StreamDataset._insert_data(adcp_ds, param, data)
        self.assertIn('corrected_echo_intensity_beam1', adcp_ds)

        self.assertEqual(set(adcp_ds.corrected_echo_intensity_beam1.dims), {'obs', 'bin'})

        filled = np.zeros_like(adcp_ds.velocity_beam1)
        filled[:] = param.fill_value
        np.testing.assert_equal(adcp_ds.corrected_echo_intensity_beam1, filled)

    def test_insert_bad_shape_array_data(self):
        adcp_fn = 'deployment0000_RS03AXBS-LJ03A-10-ADCPTE301-streamed-adcp_velocity_beam.nc'
        adcp_ds = xr.open_dataset(os.path.join(DATA_DIR, adcp_fn), decode_times=False)

        data = np.zeros_like(adcp_ds.time)
        param = Parameter.query.get(2769)

        with self.assertRaises(ValueError):
            StreamDataset._insert_data(adcp_ds, param, data)

        self.assertNotIn('corrected_echo_intensity_beam1', adcp_ds)

    def test_provenance_as_netcdf_attribute(self):
        ctd_ds = xr.open_dataset(os.path.join(DATA_DIR, self.ctdpf_fn), decode_times=False)
        ctd_ds = ctd_ds[['obs', 'time', 'deployment', 'temperature', 'pressure',
                         'pressure_temp', 'conductivity', 'ext_volt0']]

        ctd_stream_dataset = StreamDataset(self.ctdpf_sk, {}, [], 'UNIT')
        ctd_stream_dataset.events = self.ctd_events
        ctd_stream_dataset._insert_dataset(ctd_ds)
        ctd_stream_dataset.insert_instrument_attributes()
        for ds in ctd_stream_dataset.datasets.itervalues():
            self.assertIn('Manufacturer', ds.attrs)
            self.assertIn('ModelNumber', ds.attrs)
            self.assertIn('SerialNumber', ds.attrs)
            self.assertIn('Description', ds.attrs)
            self.assertIn('FirmwareVersion', ds.attrs)
            self.assertIn('SoftwareVersion', ds.attrs)
            self.assertIn('AssetUniqueID', ds.attrs)
            self.assertIn('Notes', ds.attrs)
            self.assertIn('Owner', ds.attrs)
            self.assertIn('RemoteResources', ds.attrs)
            self.assertIn('ShelfLifeExpirationDate', ds.attrs)
            self.assertIn('Mobile', ds.attrs)
            self.assertIn('AssetManagementRecordLastModified', ds.attrs)

            self.assertEqual(ds.attrs['Manufacturer'], 'Sea-Bird Electronics')
            self.assertEqual(ds.attrs['ModelNumber'], 'SBE 16plus V2')
            self.assertEqual(ds.attrs['SerialNumber'], '16-50112')
            self.assertEqual(ds.attrs['Description'], 'CTD Profiler: CTDPF Series A')
            self.assertEqual(ds.attrs['AssetUniqueID'], 'ATOSU-66662-00013')
            self.assertEqual(ds.attrs['Mobile'], 'False')
            self.assertEqual(ds.attrs['AssetManagementRecordLastModified'], '2017-04-03T23:48:25.650000')

            self.assertEqual(ds.attrs['FirmwareVersion'], 'Not specified.')
            self.assertEqual(ds.attrs['SoftwareVersion'], 'Not specified.')
            self.assertEqual(ds.attrs['Notes'], 'Not specified.')
            self.assertEqual(ds.attrs['Owner'], 'Not specified.')
            self.assertEqual(ds.attrs['RemoteResources'], '[]')
            self.assertEqual(ds.attrs['ShelfLifeExpirationDate'], 'Not specified.')

    def test_provenance_as_netcdf_attribute_missing(self):
        ctd_ds = xr.open_dataset(os.path.join(DATA_DIR, self.ctdpf_fn), decode_times=False)
        ctd_ds = ctd_ds[['obs', 'time', 'deployment', 'temperature', 'pressure',
                         'pressure_temp', 'conductivity', 'ext_volt0']]

        ctd_stream_dataset = StreamDataset(self.ctdpf_sk, {}, [], 'UNIT')
        ctd_stream_dataset.events = copy.deepcopy(self.ctd_events)
        ctd_stream_dataset.events.deps = {}
        ctd_stream_dataset._insert_dataset(ctd_ds)
        ctd_stream_dataset.insert_instrument_attributes()


