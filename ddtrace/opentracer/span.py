import time
import threading

from opentracing import Span as OpenTracingSpan
from ddtrace.span import Span as DatadogSpan
from ddtrace.ext import errors
from .tags import OTTags

from .span_context import SpanContext


class SpanLogRecord(object):
    """A representation of a log record."""

    slots = ['record', 'timestamp']

    def __init__(self, key_values, timestamp=None):
        self.timestamp = timestamp or time.time()
        self.record = key_values


class SpanLog(object):
    """A collection of log records."""

    slots = ['records']

    def __init__(self):
        self.records = []

    def add_record(self, key_values, timestamp=None):
        self.records.append(SpanLogRecord(key_values, timestamp))

    def __len__(self):
        return len(self.records)

    def __getitem__(self, key):
        if type(key) is int:
            return self.records[key]
        else:
            raise TypeError('only indexing by int is currently supported')


class Span(OpenTracingSpan):
    """Datadog implementation of :class:`opentracing.Span`"""

    def __init__(self, tracer, context, operation_name):
        if context is not None:
            context = SpanContext(ddcontext=context._dd_context,
                                  baggage=context.baggage)
        else:
            context = SpanContext()

        super(Span, self).__init__(tracer, context)

        self.log = SpanLog()
        self.finished = False
        self.lock = threading.Lock()
        # use a datadog span
        self._dd_span = DatadogSpan(tracer._dd_tracer, operation_name,
                                    context=context._dd_context)

    def finish(self, finish_time=None):
        """Finish the span.

        This calls finish on the ddspan.

        :param finish_time: specify a custom finish time with a unix timestamp
            per time.time()
        :type timestamp: float
        """
        if self.finished:
            return

        # finish the datadog span
        self._dd_span.finish(finish_time)
        self.finished = True

    def set_baggage_item(self, key, value):
        """Sets a baggage item in the span context of this span.

        Baggage is used to propagate state between spans.

        :param key: baggage item key
        :type key: str

        :param value: baggage item value
        :type value: a type that can be compat.stringify()'d

        :rtype: Span
        :return: itself for chaining calls
        """
        new_ctx = self.context.with_baggage_item(key, value)
        self.set_tag(key, value)
        with self.lock:
            self._context = new_ctx
        return self

    def get_baggage_item(self, key):
        """Gets a baggage item from the span context of this span.

        :param key: baggage item key
        :type key: str

        :rtype: str
        :return: the baggage value for the given key or ``None``.
        """
        return self.context.get_baggage_item(key)

    def set_operation_name(self, operation_name):
        """Set the operation name."""
        self._dd_span.name = operation_name

    def log_kv(self, key_values, timestamp=None):
        """Add a log record to this span.

        Passes on relevant opentracing key values onto the datadog span.

        :param key_values: a dict of string keys and values of any type
        :type key_values: dict

        :param timestamp: a unix timestamp per time.time()
        :type timestamp: float

        :return: the span itself, for call chaining
        :rtype: Span
        """
        # add the record to the log
        # TODO: there really isn't any functionality provided in ddtrace
        #       (or even opentracing) for logging
        self.log.add_record(key_values, timestamp)

        # match opentracing defined keys to datadog functionality
        # opentracing/specification/blob/1be630515dafd4d2a468d083300900f89f28e24d/semantic_conventions.md#log-fields-table
        for key, val in key_values.items():
            if key == 'event' and val == 'error':
                # TODO: not sure if it's actually necessary to set the error manually
                self._dd_span.error = 1
                self.set_tag('error', 1)
            elif key == 'error' or key == 'error.object':
                self.set_tag(errors.ERROR_TYPE, val)
            elif key == 'message':
                self.set_tag(errors.ERROR_MSG, val)
            elif key == 'stack':
                self.set_tag(errors.ERROR_STACK, val)
            else:
                pass

        return self

    def set_tag(self, key, value):
        """Set a tag on the span.

        This sets the tag on the underlying datadog span.
        """
        if key == OTTags.SPAN_TYPE:
            self._dd_span.span_type = value
        elif key == OTTags.HTTP_URL or key == OTTags.DB_STATEMENT:
            # TODO: there may be cardinality issues with this?
            self._dd_span.resource = value
        else:
            self._dd_span.set_tag(key, value)

    def _get_tag(self, key):
        """Gets a tag from the span.

        This method retrieves the tag from the underlying datadog span.
        """
        return self._dd_span.get_tag(key)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self._dd_span.set_exc_info(exc_type, exc_val, exc_tb)

        # note: self.finish() AND _dd_span.__exit__ will call _span.finish() but
        # it is idempotent
        self._dd_span.__exit__(exc_type, exc_val, exc_tb)
        self.finish()

    def _add_dd_span(self, ddspan):
        """Associates a datadog span with this span."""
        # get the datadog span context
        self._dd_span = ddspan
        self.context._dd_context = ddspan.context
        # set any baggage tags in the ddspan
        for key, val in self.context.baggage.items():
            self.set_tag(key, val)

    @property
    def _dd_context(self):
        return self._dd_span.context
