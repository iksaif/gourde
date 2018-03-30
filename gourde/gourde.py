#!/usr/bin/env python

import argparse
import flask
import logging
import os
import sys

import pkg_resources

from prometheus_flask_exporter import PrometheusMetrics

try:
    from raven.contrib.flask import Sentry
except ImportError:
    Sentry = None


class Error(Exception):

    """All local errors."""
    pass


class Gourde(object):

    """Wrapper around Flask."""

    LOG_FORMAT = (
        '[%(asctime)s] %(levelname)s %(module)s '
        '[%(filename)s:%(funcName)s:%(lineno)d] (%(thread)d): %(message)s'
    )

    def __init__(self, app_or_name, registry=None):
        """Build a new Gourde.

        Args:
            Either a flask.Flask or the name of the calling module.
        """
        if isinstance(app_or_name, flask.Flask):
            self.app = app_or_name
        else:
            # Convenience constructor.
            self.app = flask.Flask(app_or_name)

        self.host = '127.0.0.1'
        self.port = 8080
        self.debug = False
        self.log_level = None
        self.twisted = False
        self.threads = None
        self.metrics = None
        self.is_setup = False

        self.add_url_rule('/', 'status', self.status)
        self.add_url_rule('/-/healthy', 'health', self.healthy)
        self.add_url_rule('/-/ready', 'ready', self.ready)
        if self.app.has_static_folder:
            self.add_url_rule('/favicon.ico', 'favicon', self.favicon)

        self.setup_prometheus(registry)
        self.setup_sentry(sentry_dsn=None)

    def setup(self, args):
        if args is None:
            parser = self.get_argparser()
            args = parser.parse_args()
        self.host = args.host
        self.port = args.port
        self.debug = args.debug
        self.log_level = args.log_level
        self.twisted = args.twisted
        self.threads = args.threads
        self.setup_logging(self.log_level)
        self.is_setup = True

    @staticmethod
    def get_argparser(parser=None):
        """Customize a parser to get the correct options."""
        parser = parser or argparse.ArgumentParser()
        parser.add_argument('--host', help='Host listen address')
        parser.add_argument(
            '--port', '-p', default=9050, help='Listen port', type=int)
        parser.add_argument(
            '--debug', '-d', default=False, action='store_true', help='Enable debug mode')
        parser.add_argument(
            '--log-level', '-l', default='INFO', help='Log Level, empty string to disable.')
        parser.add_argument(
            '--twisted', default=False, action='store_true', help='Use twisted to server requests.')
        parser.add_argument(
            '--threads', default=None, help='Number of threads to use.', type=int)
        return parser

    def setup_logging(self, log_level):
        """Setup logging."""
        if not log_level:
            return

        # Remove existing logger.
        self.app.config['LOGGER_HANDLER_POLICY'] = 'never'
        self.app.logger.propagate = True

        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(self.LOG_FORMAT))
        self.app.logger.addHandler(handler)
        self.app.logger.setLevel(logging.getLevelName(log_level))
        self.app.logger.info("Logging initialized.")

    def setup_prometheus(self, registry):
        """Setup Prometheus."""
        self.metrics = PrometheusMetrics(self.app, registry=registry)
        try:
            version = pkg_resources.require(self.app.name)[0].version
        except pkg_resources.DistributionNotFound:
            version = 'unknown'
        self.metrics.info(
            'app_info', 'Application info', version=version, appname=self.app.name)
        self.app.logger.info("Prometheus is enabled.")

    def setup_sentry(self, sentry_dsn):
        sentry_dsn = sentry_dsn or os.getenv('SENTRY_DSN', None)
        if not Sentry or not sentry_dsn:
            return

        sentry = Sentry(dsn=sentry_dsn)
        sentry.init_app(self.app)
        self.app.logger.info("Sentry is enabled.")

    def add_url_rule(self, route, endpoint, handler):
        """Add a new url route.

        Args:
            See flask.Flask.add_url_route().
        """
        self.app.add_url_rule(route, endpoint, handler)

    def status(self):
        return 'status'

    def is_healthy(self):
        return True

    def healthy(self):
        """Return 200 is healthy, else 500.

        Override is_healthy() to change the health check.
        """
        try:
            if self.is_healthy():
                return 'OK', 200
            else:
                return 'FAIL', 500
        except Exception as e:
            self.app.logger.exception()
            return str(e), 500

    def is_ready(self):
        return True

    def ready(self):
        """Return 200 is ready, else 500.

        Override is_ready() to change the readiness check.
        """
        try:
            if self.is_ready():
                return 'OK', 200
            else:
                return 'FAIL', 500
        except Exception as e:
            self.app.logger.exception()
            return str(e), 500

    def favicon(self):
        return flask.send_from_directory(
            self.app.static_folder,
            'favicon.ico',
            mimetype='image/vnd.microsoft.icon'
        )

    def run(self, **options):
        """Run the application."""
        if not self.is_setup:
            self.setup()
        if not self.twisted:
            self.run_with_werkzeug(**options)
        else:
            self.run_with_twisted(**options)

    def run_with_werkzeug(self, **options):
        """Run with werkzeug simple wsgi container."""
        threaded = self.threads is not None and (self.threads > 0)
        self.app.run(host=self.host, port=self.port,
                     debug=self.debug, threaded=threaded, **options)

    def run_with_twisted(self, **options):
        """Run with twisted."""
        from twisted.internet import reactor
        from twisted.python import log
        import flask_twisted

        twisted = flask_twisted.Twisted(self.app)
        if self.threads:
            reactor.suggestThreadPoolSize(self.threads)
        if self.log_level:
            log.startLogging(sys.stderr)
        twisted.run(
            host=self.host, port=self.port, debug=self.debug, **options)