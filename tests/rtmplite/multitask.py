################################################################################
#
# Copyright (c) 2007 Christopher J. Stawarz
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT.  IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
#
################################################################################



"""

Cooperative multitasking and asynchronous I/O using generators

multitask allows Python programs to use generators (a.k.a. coroutines)
to perform cooperative multitasking and asynchronous I/O.
Applications written using multitask consist of a set of cooperating
tasks that yield to a shared task manager whenever they perform a
(potentially) blocking operation, such as I/O on a socket or getting
data from a queue.  The task manager temporarily suspends the task
(allowing other tasks to run in the meantime) and then restarts it
when the blocking operation is complete.  Such an approach is suitable
for applications that would otherwise have to use select() and/or
multiple threads to achieve concurrency.

The functions and classes in the multitask module allow tasks to yield
for I/O operations on sockets and file descriptors, adding/removing
data to/from queues, or sleeping for a specified interval.  When
yielding, a task can also specify a timeout.  If the operation for
which the task yielded has not completed after the given number of
seconds, the task is restarted, and a Timeout exception is raised at
the point of yielding.

As a very simple example, here's how one could use multitask to allow
two unrelated tasks to run concurrently:

  >>> def printer(message):
  ...     while True:
  ...         print message
  ...         yield
  ... 
  >>> multitask.add(printer('hello'))
  >>> multitask.add(printer('goodbye'))
  >>> multitask.run()
  hello
  goodbye
  hello
  goodbye
  hello
  goodbye
  [and so on ...]

For a more useful example, here's how one could implement a
multitasking server that can handle multiple concurrent client
connections:

  def listener(sock):
      while True:
          conn, address = (yield multitask.accept(sock))
          multitask.add(client_handler(conn))

  def client_handler(sock):
      while True:
          request = (yield multitask.recv(sock, 1024))
          if not request:
              break
          response = handle_request(request)
          yield multitask.send(sock, response)

  multitask.add(listener(sock))
  multitask.run()

Tasks can also yield other tasks, which allows for composition of
tasks and reuse of existing multitasking code.  A child task runs
until it either completes or raises an exception.  To return output to
its parent, a child task raises StopIteration, passing the output
value(s) to the StopIteration constructor.  An unhandled exception
raised within a child task is propagated to its parent.  For example:

  >>> def parent():
  ...     print (yield return_none())
  ...     print (yield return_one())
  ...     print (yield return_many())
  ...     try:
  ...         yield raise_exception()
  ...     except Exception, e:
  ...         print 'caught exception: %s' % e
  ... 
  >>> def return_none():
  ...     yield
  ...     # do nothing
  ...     # or return
  ...     # or raise StopIteration
  ...     # or raise StopIteration(None)
  ... 
  >>> def return_one():
  ...     yield
  ...     raise StopIteration(1)
  ... 
  >>> def return_many():
  ...     yield
  ...     raise StopIteration(2, 3)  # or raise StopIteration((2, 3))
  ... 
  >>> def raise_exception():
  ...     yield
  ...     raise RuntimeError('foo')
  ... 
  >>> multitask.add(parent())
  >>> multitask.run()
  None
  1
  (2, 3)
  caught exception: foo

"""


import collections
import errno
from functools import partial
import heapq
import os
import select
import sys
import time
import types


__author__   = 'Christopher Stawarz <cstawarz@csail.mit.edu>'
__version__  = '0.2.0'
# __revision__ = int('$Revision$'.split()[1])



################################################################################
#
# Timeout exception type
#
################################################################################



class Timeout(Exception):
    'Raised in a yielding task when an operation times out'
    pass



################################################################################
#
# _ChildTask class
#
################################################################################



class _ChildTask(object):

    def __init__(self, parent, task):
        self.parent = parent
        self.task = task

    def send(self, value):
        return self.task.send(value)

    def throw(self, type, value=None, traceback=None):
        return self.task.throw(type, value, traceback)



################################################################################
#
# YieldCondition class
#
################################################################################



class YieldCondition(object):

    """

    Base class for objects that are yielded by a task to the task
    manager and specify the condition(s) under which the task should
    be restarted.  Only subclasses of this class are useful to
    application code.

    """

    def __init__(self, timeout=None):
        """

        If timeout is None, the task will be suspended indefinitely
        until the condition is met.  Otherwise, if the condition is
        not met within timeout seconds, a Timeout exception will be
        raised in the yielding task.

        """

        self.task = None
        self.handle_expiration = None

        if timeout is None:
            self.expiration = None
        else:
            self.expiration = time.time() + float(timeout)

    def _expires(self):
        return (self.expiration is not None)



################################################################################
#
# _SleepDelay class and related functions
#
################################################################################



class _SleepDelay(YieldCondition):

    def __init__(self, seconds):
        seconds = float(seconds)
        if seconds <= 0.0:
            raise ValueError("'seconds' must be greater than 0")
        super(_SleepDelay, self).__init__(seconds)


def sleep(seconds):
    """

    A task that yields the result of this function will be resumed
    after the specified number of seconds have elapsed.  For example:

      while too_early():
          yield sleep(5)  # Sleep for five seconds
      do_something()      # Done sleeping; get back to work

    """

    return _SleepDelay(seconds)



################################################################################
#
# FDReady class and related functions
#
################################################################################



class FDReady(YieldCondition):

    """

    A task that yields an instance of this class will be suspended
    until a specified file descriptor is ready for I/O.

    """

    def __init__(self, fd, read=False, write=False, exc=False, timeout=None):
        """

        Resume the yielding task when fd is ready for reading,
        writing, and/or "exceptional" condition handling.  fd can be
        any object accepted by select.select() (meaning an integer or
        an object with a fileno() method that returns an integer).
        Any exception raised by select() due to fd will be re-raised
        in the yielding task.

        If timeout is not None, a Timeout exception will be raised in
        the yielding task if fd is not ready after timeout seconds
        have elapsed.

        """

        super(FDReady, self).__init__(timeout)

        self.fd = (fd if _is_file_descriptor(fd) else fd.fileno())

        if not (read or write or exc):
            raise ValueError("'read', 'write', and 'exc' cannot all be false")
        self.read = read
        self.write = write
        self.exc = exc

    def fileno(self):
        'Return the file descriptor on which the yielding task is waiting'
        return self.fd

    def _add_to_fdsets(self, read_fds, write_fds, exc_fds):
        for add, fdset in ((self.read, read_fds),
                           (self.write, write_fds),
                           (self.exc, exc_fds)):
            if add:
                fdset.add(self)

    def _remove_from_fdsets(self, read_fds, write_fds, exc_fds):
        for fdset in (read_fds, write_fds, exc_fds):
            fdset.discard(self)


def _is_file_descriptor(fd):
    return isinstance(fd, (int, long))


def readable(fd, timeout=None):
    """

    A task that yields the result of this function will be resumed
    when fd is readable.  If timeout is not None, a Timeout exception
    will be raised in the yielding task if fd is not readable after
    timeout seconds have elapsed.  For example:

      try:
          yield readable(sock, timeout=5)
          data = sock.recv(1024)
      except Timeout:
          # No data after 5 seconds

    """

    return FDReady(fd, read=True, timeout=timeout)


def writable(fd, timeout=None):
    """

    A task that yields the result of this function will be resumed
    when fd is writable.  If timeout is not None, a Timeout exception
    will be raised in the yielding task if fd is not writable after
    timeout seconds have elapsed.  For example:

      try:
          yield writable(sock, timeout=5)
          nsent = sock.send(data)
      except Timeout:
          # Can't send after 5 seconds

    """

    return FDReady(fd, write=True, timeout=timeout)



################################################################################
#
# FDAction class and related functions
#
################################################################################



class FDAction(FDReady):

    """

    A task that yields an instance of this class will be suspended
    until an I/O operation on a specified file descriptor is complete.

    """

    def __init__(self, fd, func, args=(), kwargs={}, read=False, write=False,
                 exc=False):
        """

        Resume the yielding task when fd is ready for reading,
        writing, and/or "exceptional" condition handling.  fd can be
        any object accepted by select.select() (meaning an integer or
        an object with a fileno() method that returns an integer).
        Any exception raised by select() due to fd will be re-raised
        in the yielding task.

        The value of the yield expression will be the result of
        calling func with the specified args and kwargs (which
        presumably performs a read, write, or other I/O operation on
        fd).  If func raises an exception, it will be re-raised in the
        yielding task.  Thus, FDAction is really just a convenient
        subclass of FDReady that requests that the task manager
        perform an I/O operation on the calling task's behalf.

        If kwargs contains a timeout argument that is not None, a
        Timeout exception will be raised in the yielding task if fd is
        not ready after timeout seconds have elapsed.

        """

        timeout = kwargs.pop('timeout', None)
        super(FDAction, self).__init__(fd, read, write, exc, timeout)

        self.func = func
        self.args = args
        self.kwargs = kwargs

    def _eval(self):
        return self.func(*(self.args), **(self.kwargs))


def read(fd, *args, **kwargs):
    """

    A task that yields the result of this function will be resumed
    when fd is readable, and the value of the yield expression will be
    the result of reading from fd.  If a timeout keyword is given and
    is not None, a Timeout exception will be raised in the yielding
    task if fd is not readable after timeout seconds have elapsed.
    Other arguments will be passed to the read function (os.read() if
    fd is an integer, fd.read() otherwise).  For example:

      try:
          data = (yield read(fd, 1024, timeout=5))
      except Timeout:
          # No data after 5 seconds

    """

    func = (partial(os.read, fd) if _is_file_descriptor(fd) else fd.read)
    return FDAction(fd, func, args, kwargs, read=True)


def readline(fd, *args, **kwargs):
    """

    A task that yields the result of this function will be resumed
    when fd is readable, and the value of the yield expression will be
    the result of reading a line from fd.  If a timeout keyword is
    given and is not None, a Timeout exception will be raised in the
    yielding task if fd is not readable after timeout seconds have
    elapsed.  Other arguments will be passed to fd.readline().  For
    example:

      try:
          data = (yield readline(fd, timeout=5))
      except Timeout:
          # No data after 5 seconds

    """

    return FDAction(fd, fd.readline, args, kwargs, read=True)


def write(fd, *args, **kwargs):
    """

    A task that yields the result of this function will be resumed
    when fd is writable, and the value of the yield expression will be
    the result of writing to fd.  If a timeout keyword is given and is
    not None, a Timeout exception will be raised in the yielding task
    if fd is not writable after timeout seconds have elapsed.  Other
    arguments will be passed to the write function (os.write() if fd
    is an integer, fd.write() otherwise).  For example:

      try:
          nbytes = (yield write(fd, data, timeout=5))
      except Timeout:
          # Can't write after 5 seconds

    """

    func = (partial(os.write, fd) if _is_file_descriptor(fd) else fd.write)
    return FDAction(fd, func, args, kwargs, write=True)


def accept(sock, *args, **kwargs):
    """

    A task that yields the result of this function will be resumed
    when sock is readable, and the value of the yield expression will
    be the result of accepting a new connection on sock.  If a timeout
    keyword is given and is not None, a Timeout exception will be
    raised in the yielding task if sock is not readable after timeout
    seconds have elapsed.  Other arguments will be passed to
    sock.accept().  For example:

      try:
          conn, address = (yield accept(sock, timeout=5))
      except Timeout:
          # No connections after 5 seconds

    """

    return FDAction(sock, sock.accept, args, kwargs, read=True)


def recv(sock, *args, **kwargs):
    """

    A task that yields the result of this function will be resumed
    when sock is readable, and the value of the yield expression will
    be the result of receiving from sock.  If a timeout keyword is
    given and is not None, a Timeout exception will be raised in the
    yielding task if sock is not readable after timeout seconds have
    elapsed.  Other arguments will be passed to sock.recv().  For
    example:

      try:
          data = (yield recv(sock, 1024, timeout=5))
      except Timeout:
          # No data after 5 seconds

    """

    return FDAction(sock, sock.recv, args, kwargs, read=True)


def recvfrom(sock, *args, **kwargs):
    """

    A task that yields the result of this function will be resumed
    when sock is readable, and the value of the yield expression will
    be the result of receiving from sock.  If a timeout keyword is
    given and is not None, a Timeout exception will be raised in the
    yielding task if sock is not readable after timeout seconds have
    elapsed.  Other arguments will be passed to sock.recvfrom().  For
    example:

      try:
          data, address = (yield recvfrom(sock, 1024, timeout=5))
      except Timeout:
          # No data after 5 seconds

    """

    return FDAction(sock, sock.recvfrom, args, kwargs, read=True)


def send(sock, *args, **kwargs):
    """

    A task that yields the result of this function will be resumed
    when sock is writable, and the value of the yield expression will
    be the result of sending to sock.  If a timeout keyword is given
    and is not None, a Timeout exception will be raised in the
    yielding task if sock is not writable after timeout seconds have
    elapsed.  Other arguments will be passed to the sock.send().  For
    example:

      try:
          nsent = (yield send(sock, data, timeout=5))
      except Timeout:
          # Can't send after 5 seconds

    """

    return FDAction(sock, sock.send, args, kwargs, write=True)


def sendto(sock, *args, **kwargs):
    """

    A task that yields the result of this function will be resumed
    when sock is writable, and the value of the yield expression will
    be the result of sending to sock.  If a timeout keyword is given
    and is not None, a Timeout exception will be raised in the
    yielding task if sock is not writable after timeout seconds have
    elapsed.  Other arguments will be passed to the sock.sendto().
    For example:

      try:
          nsent = (yield sendto(sock, data, address, timeout=5))
      except Timeout:
          # Can't send after 5 seconds

    """

    return FDAction(sock, sock.sendto, args, kwargs, write=True)



################################################################################
#
# Queue and _QueueAction classes
#
################################################################################



class Queue(object):

    """

    A multi-producer, multi-consumer FIFO queue (similar to
    Queue.Queue) that can be used for exchanging data between tasks

    """

    def __init__(self, contents=(), maxsize=0):
        """

        Create a new Queue instance.  contents is a sequence (empty by
        default) containing the initial contents of the queue.  If
        maxsize is greater than 0, the queue will hold a maximum of
        maxsize items, and put() will block until space is available
        in the queue.

        """

        self.maxsize = int(maxsize)
        self._queue = collections.deque(contents)

    def __len__(self):
        'Return the number of items in the queue'
        return len(self._queue)

    def _get(self):
        return self._queue.popleft()

    def _put(self, item):
        self._queue.append(item)

    def empty(self):
        'Return True is the queue is empty, False otherwise'
        return (len(self) == 0)

    def full(self):
        'Return True is the queue is full, False otherwise'
        return ((len(self) >= self.maxsize) if (self.maxsize > 0) else False)

    def get(self, timeout=None):
        """

        A task that yields the result of this method will be resumed
        when an item is available in the queue, and the value of the
        yield expression will be the item.  If timeout is not None, a
        Timeout exception will be raised in the yielding task if an
        item is not available after timeout seconds have elapsed.  For
        example:

          try:
              item = (yield queue.get(timeout=5))
          except Timeout:
              # No item available after 5 seconds

        """

        return _QueueAction(self, timeout=timeout)

    def put(self, item, timeout=None):
        """

        A task that yields the result of this method will be resumed
        when item has been added to the queue.  If timeout is not
        None, a Timeout exception will be raised in the yielding task
        if no space is available after timeout seconds have elapsed.
        For example:

          try:
              yield queue.put(item, timeout=5)
          except Timeout:
              # No space available after 5 seconds

        """

        return _QueueAction(self, item, timeout=timeout)


class _QueueAction(YieldCondition):

    NO_ITEM = object()

    def __init__(self, queue, item=NO_ITEM, timeout=None):
        super(_QueueAction, self).__init__(timeout)
        if not isinstance(queue, Queue):
            raise TypeError("'queue' must be a Queue instance")
        self.queue = queue
        self.item = item


################################################################################
#
# SmartQueue and _SmartQueueAction classes
#
################################################################################



class SmartQueue(object):

    """

    A multi-producer, multi-consumer FIFO queue (similar to
    Queue.Queue) that can be used for exchanging data between tasks.
    The difference with Queue is that this implements filtering criteria
    on get and allows multiple get to be signalled for the same put. 
    On the downside, this uses list instead of deque and has lower
    performance.
    
    """

    def __init__(self, contents=(), maxsize=0):
        """

        Create a new Queue instance.  contents is a sequence (empty by
        default) containing the initial contents of the queue.  If
        maxsize is greater than 0, the queue will hold a maximum of
        maxsize items, and put() will block until space is available
        in the queue.

        """

        self.maxsize = int(maxsize)
        self._pending =  list(contents)

    def __len__(self):
        'Return the number of items in the queue'
        return len(self._pending)

    def _get(self, criteria=None):
        #self._pending = filter(lambda x: x[1]<=now, self._pending) # remove expired ones
        if criteria:
            found = filter(lambda x: criteria(x), self._pending)   # check any matching criteria
            if found: 
                self._pending.remove(found[0])
                return found[0]
            else:
                return None
        else:
            return self._pending.pop(0) if self._pending else None

    def _put(self, item):
        self._pending.append(item)

    def empty(self):
        'Return True is the queue is empty, False otherwise'
        return (len(self) == 0)

    def full(self):
        'Return True is the queue is full, False otherwise'
        return ((len(self) >= self.maxsize) if (self.maxsize > 0) else False)

    def get(self, timeout=None, criteria=None):
        """

        A task that yields the result of this method will be resumed
        when an item is available in the queue and the item matches the
        given criteria (a function, usually lambda), and the value of the
        yield expression will be the item.  If timeout is not None, a
        Timeout exception will be raised in the yielding task if an
        item is not available after timeout seconds have elapsed.  For
        example:

          try:
              item = (yield queue.get(timeout=5, criteria=lambda x: x.name='kundan'))
          except Timeout:
              # No item available after 5 seconds

        """

        return _SmartQueueAction(self, timeout=timeout, criteria=criteria)

    def put(self, item, timeout=None):
        """

        A task that yields the result of this method will be resumed
        when item has been added to the queue.  If timeout is not
        None, a Timeout exception will be raised in the yielding task
        if no space is available after timeout seconds have elapsed.
        TODO: Otherwise if space is available, the timeout specifies how 
        long to keep the item in the queue before discarding it if it
        is not fetched in a get. In this case it doesnot throw exception. 
        For example:

          try:
              yield queue.put(item, timeout=5)
          except Timeout:
              # No space available after 5 seconds

        """

        return _SmartQueueAction(self, item, timeout=timeout)


class _SmartQueueAction(YieldCondition):

    NO_ITEM = object()

    def __init__(self, queue, item=NO_ITEM, timeout=None, criteria=None):
        super(_SmartQueueAction, self).__init__(timeout)
        if not isinstance(queue, SmartQueue):
            raise TypeError("'queue' must be a SmartQueue instance")
        self.queue = queue
        self.item = item
        self.criteria = criteria
        self.expires = (timeout is not None) and (time.time() + timeout) or 0


################################################################################
#
# TaskManager class
#
################################################################################



class TaskManager(object):

    """

    Engine for running a set of cooperatively-multitasking tasks
    within a single Python thread

    """

    def __init__(self):
        """

        Create a new TaskManager instance.  Generally, there will only
        be one of these per Python process.  If you want to run two
        existing instances simultaneously, merge them first, then run
        one or the other.

        """

        self._queue       = collections.deque()
        self._read_waits  = set()
        self._write_waits = set()
        self._exc_waits   = set()
        self._queue_waits = collections.defaultdict(self._double_deque)
        self._timeouts    = []

    @staticmethod
    def _double_deque():
        return (collections.deque(), collections.deque())

    def merge(self, other):
        """

        Merge this TaskManager with another.  After the merge, the two
        objects share the same (merged) internal data structures, so
        either can be used to manage the combined task set.

        """

        if not isinstance(other, TaskManager):
            raise TypeError("'other' must be a TaskManager instance")

        # Merge the data structures
        self._queue.extend(other._queue)
        self._read_waits  |= other._read_waits
        self._write_waits |= other._write_waits
        self._exc_waits   |= other._exc_waits
        self._queue_waits.update(other._queue_waits)
        self._timeouts.extend(other._timeouts)
        heapq.heapify(self._timeouts)

        # Make other reference the merged data structures.  This is
        # necessary because other's tasks may reference and use other
        # (e.g. to add a new task in response to an event).
        other._queue       = self._queue
        other._read_waits  = self._read_waits
        other._write_waits = self._write_waits
        other._exc_waits   = self._exc_waits
        other._queue_waits = self._queue_waits
        other._timeouts    = self._timeouts

    def add(self, task):
        'Add a new task (i.e. a generator instance) to the run queue'

        if not isinstance(task, types.GeneratorType):
            raise TypeError("'task' must be a generator")
        self._enqueue(task)

    def _enqueue(self, task, input=None, exc_info=()):
        self._queue.append((task, input, exc_info))

    def run(self):
        """

        Call run_next() repeatedly until there are no tasks that are
        currently runnable, waiting for I/O, or waiting to time out.
        Note that this method can block indefinitely (e.g. if there
        are only I/O waits and no timeouts).  If this is unacceptable,
        use run_next() instead.

        """
        while self.has_runnable() or self.has_io_waits() or self.has_timeouts():
            self.run_next()

    def has_runnable(self):
        """

        Return True is there are runnable tasks in the queue, False
        otherwise

        """
        return bool(self._queue)

    def has_io_waits(self):
        """

        Return True is there are tasks waiting for I/O, False
        otherwise

        """
        return bool(self._read_waits or self._write_waits or self._exc_waits)

    def has_timeouts(self):
        """

        Return True is there are tasks with pending timeouts, False
        otherwise

        """
        return bool(self._timeouts)

    def run_next(self, timeout=None):
        """

        Perform one iteration of the run cycle: check whether any
        pending I/O operations can be performed, check whether any
        timeouts have expired, then run all currently runnable tasks.

        The timeout argument specifies the maximum time to wait for
        some task to become runnable.  If timeout is None and there
        are no currently runnable tasks, but there are tasks waiting
        to perform I/O or time out, then this method will block until
        at least one of the waiting tasks becomes runnable.  To
        prevent this method from blocking indefinitely, use timeout to
        specify the maximum number of seconds to wait.

        If there are runnable tasks in the queue when run_next() is
        called, then it will check for I/O readiness using a
        non-blocking call to select() (i.e. a poll), and only
        already-expired timeouts will be handled.  This ensures both
        that the task manager is never idle when tasks can be run and
        that tasks waiting for I/O never starve.

        """

        while self.has_io_waits():
            if self._handle_io_waits(self._fix_run_timeout(timeout)) or self.has_runnable(): break

        if self.has_timeouts():
            self._handle_timeouts(self._fix_run_timeout(timeout))

        # Run all tasks currently in the queue
        #for dummy in xrange(len(self._queue)):
        while len(self._queue) > 0:
            task, input, exc_info = self._queue.popleft()
            try:
                if exc_info:
                    output = task.throw(*exc_info)
                else:
                    output = task.send(input)
            except StopIteration, e:
                if isinstance(task, _ChildTask):
                    if not e.args:
                        output = None
                    elif len(e.args) == 1:
                        output = e.args[0]
                    else:
                        output = e.args
                    self._enqueue(task.parent, input=output)
            except:
                if isinstance(task, _ChildTask):
                    # Propagate exception to parent
                    self._enqueue(task.parent, exc_info=sys.exc_info())
                else:
                    # No parent task, so just die
                    raise
            else:
                self._handle_task_output(task, output)

    def _fix_run_timeout(self, timeout):
        if self.has_runnable():
            # Don't block if there are tasks in the queue
            timeout = 0.0
        elif self.has_timeouts():
            # If there are timeouts, block only until the first expiration
            expiration_timeout = max(0.0, self._timeouts[0][0] - time.time())
            if (timeout is None) or (timeout > expiration_timeout):
                timeout = expiration_timeout
        return timeout

    def _handle_io_waits(self, timeout):
        # The error handling here is (mostly) borrowed from Twisted
        try:
            read_ready, write_ready, exc_ready = \
                select.select(self._read_waits,
                              self._write_waits,
                              self._exc_waits,
                              timeout)
        except (TypeError, ValueError):
            self._remove_bad_file_descriptors()
            return False
        except (select.error, IOError), err:
            if err[0] == errno.EINTR:
                return False
            elif ((err[0] == errno.EBADF) or
                  ((sys.platform == 'win32') and
                   (err[0] == 10038))):  # WSAENOTSOCK
                self._remove_bad_file_descriptors()
                return False
            else:
                # Not an error we can handle, so die
                raise
        else:
            for fd in set(read_ready + write_ready + exc_ready):
                try:
                    input = (fd._eval() if isinstance(fd, FDAction) else None)
                    self._enqueue(fd.task, input=input)
                except:
                    self._enqueue(fd.task, exc_info=sys.exc_info())
                fd._remove_from_fdsets(self._read_waits,
                                       self._write_waits,
                                       self._exc_waits)
                if fd._expires():
                    self._remove_timeout(fd)
            return True

    def _remove_bad_file_descriptors(self):
        for fd in (self._read_waits | self._write_waits | self._exc_waits):
            try:
                select.select([fd], [fd], [fd], 0.0)
            except:
                # TODO: do not enqueue the exception (socket.error) so that it does not crash
                # when closing an already closed socket. See rtmplite issue #28
                # self._enqueue(fd.task, exc_info=sys.exc_info())
                fd._remove_from_fdsets(self._read_waits,
                                       self._write_waits,
                                       self._exc_waits)
                if fd._expires():
                    self._remove_timeout(fd)

    def _add_timeout(self, item, handler):
        item.handle_expiration = handler
        heapq.heappush(self._timeouts, (item.expiration, item))

    def _remove_timeout(self, item):
        self._timeouts.remove((item.expiration, item))
        heapq.heapify(self._timeouts)

    def _handle_timeouts(self, timeout):
        if (not self.has_runnable()) and (timeout > 0.0):
            time.sleep(timeout)

        current_time = time.time()

        while self._timeouts and (self._timeouts[0][0] <= current_time):
            item = heapq.heappop(self._timeouts)[1]
            if isinstance(item, _SleepDelay):
                self._enqueue(item.task)
            else:
                self._enqueue(item.task, exc_info=(Timeout,))
                item.handle_expiration()

    def _handle_task_output(self, task, output):
        if isinstance(output, types.GeneratorType):
            self._enqueue(_ChildTask(task, output))
        elif isinstance(output, YieldCondition):
            output.task = task
            if isinstance(output, _SleepDelay):
                self._add_timeout(output, None)
            elif isinstance(output, FDReady):
                self._handle_fdready(task, output)
            elif isinstance(output, _QueueAction):
                self._handle_queue_action(task, output)
            elif isinstance(output, _SmartQueueAction):
                self._handle_smart_queue_action(task, output)
        else:
            # Return any other output as input and send task to
            # end of queue
            self._enqueue(task, input=output)

    def _handle_fdready(self, task, output):
        output._add_to_fdsets(self._read_waits,
                              self._write_waits,
                              self._exc_waits)
        if output._expires():
            self._add_timeout(output,
                              (lambda:
                               output._remove_from_fdsets(self._read_waits,
                                                          self._write_waits,
                                                          self._exc_waits)))

    def _handle_queue_action(self, task, output):
        get_waits, put_waits = self._queue_waits[output.queue]

        if output.item is output.NO_ITEM:
            # Action is a get
            if output.queue.empty():
                get_waits.append(output)
                if output._expires():
                    self._add_timeout(output,
                                      (lambda: get_waits.remove(output)))
            else:
                item = output.queue._get()
                self._enqueue(task, input=item)
                if put_waits:
                    action = put_waits.popleft()
                    output.queue._put(action.item)
                    self._enqueue(action.task)
                    if action._expires():
                        self._remove_timeout(action)
        else:
            # Action is a put
            if output.queue.full():
                put_waits.append(output)
                if output._expires():
                    self._add_timeout(output,
                                      (lambda: put_waits.remove(output)))
            else:
                output.queue._put(output.item)
                self._enqueue(task)
                if get_waits:
                    action = get_waits.popleft()
                    item = output.queue._get()
                    self._enqueue(action.task, input=item)
                    if action._expires():
                        self._remove_timeout(action)


    def _handle_smart_queue_action(self, task, output):
        get_waits, put_waits = self._queue_waits[output.queue]

        if output.item is output.NO_ITEM:
            # Action is a get
            item = output.queue._get(criteria=output.criteria)
            if item is None:
                get_waits.append(output)
                if output._expires():
                    self._add_timeout(output,
                                      (lambda: get_waits.remove(output)))
            else:
                self._enqueue(task, input=item)
                if put_waits:
                    action = put_waits.popleft()
                    output.queue._put(action.item)
                    self._enqueue(action.task)
                    if action._expires():
                        self._remove_timeout(action)
        else:
            # Action is a put
            if output.queue.full():
                put_waits.append(output)
                if output._expires():
                    self._add_timeout(output,
                                      (lambda: put_waits.remove(output)))
            else:
                output.queue._put(output.item)
                self._enqueue(task)
                if get_waits:
                    actions = []
                    for action in get_waits:
                        item = output.queue._get(criteria=action.criteria)
                        if item is not None:
                            actions.append((action, item))
                    for action,item in actions:
                        get_waits.remove(action)
                        self._enqueue(action.task, input=item)
                        if action._expires():
                            self._remove_timeout(action)



################################################################################
#
# Default TaskManager instance
#
################################################################################



_default_task_manager = None


def get_default_task_manager():
    'Return the default TaskManager instance'
    global _default_task_manager
    if _default_task_manager is None:
        _default_task_manager = TaskManager()
    return _default_task_manager


def add(task):
    'Add a task to the default TaskManager instance'
    get_default_task_manager().add(task)


def run():
    'Run the default TaskManager instance'
    get_default_task_manager().run()



################################################################################
#
# Test routine
#
################################################################################



if __name__ == '__main__':
    if sys.platform == 'win32':
        # Make sure WSAStartup() is called
        import socket

    def printer(name):
        for i in xrange(1, 4):
            print '%s:\t%d' % (name, i)
            yield

    t = TaskManager()
    t.add(printer('first'))
    t.add(printer('second'))
    t.add(printer('third'))

    queue = Queue()

    def receiver():
        print 'receiver started'
        print 'receiver received: %s' % (yield queue.get())
        print 'receiver finished'

    def sender():
        print 'sender started'
        yield queue.put('from sender')
        print 'sender finished'

    def bad_descriptor():
        print 'bad_descriptor running'
        try:
            yield readable(12)
        except:
            print 'exception in bad_descriptor:', sys.exc_info()[1]

    def sleeper():
        print 'sleeper started'
        yield sleep(1)
        print 'sleeper finished'

    def timeout_immediately():
        print 'timeout_immediately running'
        try:
            yield Queue().get(timeout=0)
        except Timeout:
            print 'timeout_immediately timed out'

    t2 = TaskManager()
    t2.add(receiver())
    t2.add(bad_descriptor())
    t2.add(sender())
    t2.add(sleeper())
    t2.add(timeout_immediately())

    def parent():
        print 'child returned: %s' % ((yield child()),)
        try:
            yield child(raise_exc=True)
        except:
            print 'exception in child:', sys.exc_info()[1]

    def child(raise_exc=False):
        yield
        if raise_exc:
            raise RuntimeError('foo')
        raise StopIteration(1, 2, 3)

    t3 = TaskManager()
    t3.add(parent())

    t.merge(t2)
    t.merge(t3)
    t.run()

    assert not(t.has_runnable() or t.has_io_waits() or t.has_timeouts())
