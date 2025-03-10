import atexit
import gevent
import logging
import sys
import traceback
from datetime import datetime

from influxdb_client import InfluxDBClient
from influxdb_client.client.write_api import ASYNCHRONOUS
from locust.exception import InterruptTaskSet
from requests.exceptions import HTTPError
import locust.env

log = logging.getLogger('locust_influx')

class InfluxDBSettings:
    """
    Store influxdb settings
    """
    def __init__(
        self, 
        influx_host: str = 'localhost', 
        influx_port: int = 8086, 
        token: str = '$token', 
        org: str = 'default'
        bucket:str = 'name', 
        interval_ms: int = 1000
    ):
        self.influx_host = influx_host
        self.influx_port = influx_port
        self.token = token
        self.org = org
        self.bucket = bucket
        self.interval_ms = interval_ms
        

class InfluxDBListener:
    """
    Events listener that writes locust events to the given influxdb connection
    """

    def __init__(
            self,
            env: locust.env.Environment,
            influxDbSettings: InfluxDBSettings
    ):

        # flush related attributes
        self.cache = []
        self.stop_flag = False
        self.settings = influxDbSettings
        # influxdb settings
            # determine if worker or master
        self.node_id = 'local'
        if '--master' in sys.argv:
            self.node_id = 'master'
        if '--worker' in sys.argv:
            # TODO: Get real ID of slaves form locust somehow
            self.node_id = 'worker'

        # start background event to push data to influx
        self.flush_worker = gevent.spawn(self.__flush_cached_points_worker)
        self.test_start(0)

        events = env.events

        # requests
        events.request_success.add_listener(self.request_success)
        events.request_failure.add_listener(self.request_failure)
        # events
        events.test_stop.add_listener(self.test_stop)
        events.user_error.add_listener(self.user_error)
        events.spawning_complete.add_listener(self.spawning_complete)
        events.quitting.add_listener(self.quitting)
        # complete
        atexit.register(self.quitting)

    def request_success(self, request_type, name, response_time, response_length, **_kwargs) -> None:
        self.__listen_for_requests_events(self.node_id, 'locust_requests', request_type, name, response_time,
                                          response_length, True, None)

    def request_failure(self, request_type, name, response_time, response_length, exception, **_kwargs) -> None:
        self.__listen_for_requests_events(self.node_id, 'locust_requests', request_type, name, response_time,
                                          response_length, False, exception)

    def spawning_complete(self, user_count) -> None:
        self.__register_event(self.node_id, user_count, 'spawning_complete')

    def test_start(self, user_count) -> None:
        self.__register_event(self.node_id, 0, 'test_started')

    def test_stop(self, environment) -> None:
        self.__register_event(self.node_id, 0, 'test_stopped')

    def user_error(self, user_instance, exception, tb, **_kwargs) -> None:
        self.__listen_for_locust_errors(self.node_id, user_instance, exception, tb)

    def quitting(self, **_kwargs) -> None:
        self.__register_event(self.node_id, 0, 'quitting')
        self.last_flush_on_quitting()

    def __register_event(self, node_id: str, user_count: int, event: str, **_kwargs) -> None:
        """
        Persist locust event such as hatching started or stopped to influxdb.
        Append user_count in case that it exists
        :param node_id: The id of the node reporting the event.
        :param event: The event name or description.
        """

        time = datetime.utcnow()
        tags = {
        }
        fields = {
            'node_id': node_id,
            'event': event,
            'user_count': user_count
        }

        point = self.__make_data_point('locust_events', tags, fields, time)
        self.cache.append(point)

    def __listen_for_requests_events(self, node_id, measurement, request_type, name, response_time, response_length,
                                     success, exception) -> None:
        """
        Persist request information to influxdb.
        :param node_id: The id of the node reporting the event.
        :param measurement: The measurement where to save this point.
        :param success: Flag the info to as successful request or not
        """

        time = datetime.utcnow()
        tags = {
            'node_id': node_id,
            'request_type': request_type,
            'name': name,
            'success': success,
            'exception': repr(exception),
        }

        if isinstance(exception, HTTPError):
            tags['code'] = exception.response.status_code

        fields = {
            'response_time': response_time,
            'response_length': response_length,
            'counter': 1,  # TODO: Review the need of this field
        }
        point = self.__make_data_point(measurement, tags, fields, time)
        self.cache.append(point)

    def __listen_for_locust_errors(self, node_id, user_instance, exception: Exception = None, tb=None) -> None:
        """
        Persist locust errors to InfluxDB.
        :param node_id: The id of the node reporting the error.
        :return: None
        """

        time = datetime.utcnow()
        tags = {
            'exception_tag': repr(exception)
        }
        fields = {
            'node_id': node_id,
            'user_instance': repr(user_instance),
            'exception': repr(exception),
            'traceback': "".join(traceback.format_tb(tb)),
        }
        point = self.__make_data_point('locust_exceptions', tags, fields, time)
        self.cache.append(point)

    def __flush_cached_points_worker(self) -> None:
        """
        Background job that puts the points into the cache to be flushed according tot he interval defined.
        :param influxdb_client:
        :param interval:
        :return: None
        """
        log.info('Flush worker started.')
        while not self.stop_flag:
            self.__flush_points()
            gevent.sleep(self.settings.interval_ms / 1000)

    def __make_data_point(self, measurement: str, tags: dict, fields: dict, time: datetime) -> dict:
        """
        Create a list with a single point to be saved to influxdb.
        :param measurement: The measurement where to save this point.
        :param tags: Dictionary of tags to be saved in the measurement.
        :param fields: Dictionary of field to be saved to measurement.
        :param time: The time os this point.
        """
        return {"measurement": measurement, "tags": tags, "time": time, "fields": fields}

    def last_flush_on_quitting(self):
        self.stop_flag = True
        self.flush_worker.join()
        self.__flush_points()

    def __flush_points(self) -> None:
        """
        Write the cached data points to influxdb
        :param influxdb_client: An instance of InfluxDBClient
        :return: None
        """
        influxdb_client: InfluxDBClient
        try:
            influxdb_client = InfluxDBClient(
                url=f'http://{self.settings.influx_host}:{self.settings.influx_port}',
                token=self.settings.token, org=self.settings.org)
            write_api = influxdb_client.write_api(write_options=ASYNCHRONOUS)
        except:
            log.exception('Could not connect to influxdb')
            return

        log.info(f'Flushing points {len(self.cache)}')
        to_be_flushed = self.cache
        self.cache = []
        write_api.write(bucket=self.settings.bucket, org=self.settings.org, record=to_be_flushed)
        influxdb_client.close()

        # Can't find a way how to check success for async requests
        # if not success:
        #     log.info(success)
        #     log.error('Failed to write points to influxdb.')
        #     # If failed for any reason put back into the beginning of cache
        #     self.cache.insert(0, to_be_flushed)


