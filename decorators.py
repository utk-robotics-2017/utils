import math
import time
import signal
import sys
import functools
from queue import Queue
from threading import Thread
from inspect import signature, Signature  # 2 different things


def getter_setter_gen(name, type_):

    def getter(self):
        return getattr(self, "__" + name)

    def setter(self, value):
        if not isinstance(value, type_):
            raise TypeError("{} attribute must be set to an instance of {}".format(name, type_))
        setattr(self, "__" + name, value)

    return property(getter, setter)


void = type(None)


# decorator that forces variables from a class to be certain types
def attr_check(cls):
    rv = {}
    for key, value in cls.__dict__.items():
        if isinstance(value, type):
            value = getter_setter_gen(key, value)
        rv[key] = value
    # Creates a new class, using the modified dictionary as the class dict:
    new_cls = type(cls)(cls.__name__, cls.__bases__, rv)
    new_cls.__doc__ = cls.__doc__
    return new_cls


class StringLiteralAsAnnotationTypeException(Exception):
    def __init__(self, function_name: str, argument_name: str):
        super(StringLiteralAsAnnotationTypeException, self).__init__('Function {} argument {}'.format(function_name, argument_name))


def ignore_decorators_traceback(func):
    """ decorator that removes other decorators from traceback """
    @functools.wraps(func)
    def wrapper_ignore_exctb(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception:
            # Code to remove this decorator from traceback
            exc_type, exc_value, exc_traceback = sys.exc_info()
            try:
                exc_traceback = exc_traceback.tb_next
                exc_traceback = exc_traceback.tb_next
            except Exception:
                pass
            raise exc_type.with_traceback(exc_value, exc_traceback)
    return wrapper_ignore_exctb


def check_parameter_types(arg_name, arg_value, parameters, bound_arguments):
    # If the annotation is a string check if it is the current class name or one of the ancestors of the current class
    # and if that is the case set the annotation to the current class, otherwise leave it
    if isinstance(parameters[arg_name].annotation, str):
        # all member functions have self
        if 'self' in parameters:
          # Loop through a list of the classes in the method resolution order (the order in which ancestors will be checked when resolving a method or attribute)
            for ancestor in type(bound_arguments.arguments['self']).__mro__:
                if ancestor.__name__ == parameters[arg_name].annotation:
                    annotation = ancestor
                    break
            else:
                raise StringLiteralAsAnnotationTypeException()
        # all class methods have cls
        elif 'cls' in parameters:
            # Loop through a list of the classes in the method resolution order (the order in which ancestors will be checked when resolving a method or attribute)
            for ancestor in type(bound_arguments.arguments['cls']).__mro__:
                if ancestor.__name__ == parameters[arg_name].annotation:
                    annotation = ancestor
                    break
            else:
                raise StringLiteralAsAnnotationTypeException()
        # We are not allowing string literals as annotations except to denote that the current class or one of its parents is parameter type
        else:
            raise StringLiteralAsAnnotationTypeException()
    else:
        annotation = parameters[arg_name].annotation

    # Check if the type of the argument is correct
    if not isinstance(arg_value, annotation):
        raise TypeError()


def check_return_type(result, ra, sig, bound_arguments):
    # Check that the result is the correct type
    if ra != Signature.empty:
        if isinstance(ra, str):
            # all member functions have self
            if 'self' in sig.parameters:
                # Loop through a list of the classes in the method resolution order (the order in which ancestors will be checked when resolving a method or attribute)
                for ancestor in type(bound_arguments.arguments['self']).__mro__:
                    if ancestor.__name__ == ra:
                      return_annotation = ancestor
                      break
                else:
                    raise StringLiteralAsAnnotationTypeException()
            # all class methods have cls
            elif 'cls' in sig.parameters:
                for ancestor in bound_arguments.arguments['cls'].__mro__:
                    if ancestor.__name__ == ra:
                      return_annotation = ancestor
                      break
                else:
                    raise StringLiteralAsAnnotationTypeException()
            # We are not allowing string literals as annotations except to denote that the current class or one of its parents is parameter type
            else:
                raise StringLiteralAsAnnotationTypeException()

        else:
            return_annotation = ra
        if not isinstance(result, return_annotation):
            raise TypeError()


def type_check(f):
    ''' Decorate that check that the parameter and return types are correct'''
    @ignore_decorators_traceback
    def wrapper(*args, **kwargs):
        # Go through the arguments and make sure they are the correct type
        sig = signature(f)
        bound_arguments = sig.bind(*args, **kwargs)
        for i, (arg_name, arg_value) in enumerate(bound_arguments.arguments.items()):
            if arg_name in ['self', 'cls']:
                continue
            try:
              check_parameter_types(arg_name, arg_value, sig.parameters, bound_arguments)
            except TypeError:
                raise TypeError("{} argument {} is of type {}, but should be of type {}"
                              .format(f.__name__, arg_name, type(arg_value), sig.parameters[arg_name].annotation))
            except StringLiteralAsAnnotationTypeException:
                raise StringLiteralAsAnnotationTypeException(f.__name__, arg_name)

        result = f(*args, **kwargs)
        
        # Annotation will be a list if there are multiple return values (tuples denote multiple possibly types for an individual value)
        if sig.return_annotation == Signature.empty:
          pass
        elif isinstance(sig.return_annotation, list):
            for i, ra in enumerate(sig.return_annotation):
                try:
                    check_return_type(result[i], ra, sig, bound_arguments)
                except StringLiteralAsAnnotationTypeException:
                    raise StringLiteralAsAnnotationTypeException(f.__name__, 'return_value[{0:d}]'.format(i))
                except TypeError:
                    raise TypeError("{} return value[{}] {} is of type {}, but should be of type {}"
                                    .format(f.__name__, i, result, type(result), sig.return_annotation))
                  
        else:
            try:
                check_return_type(result, sig.return_annotation, sig, bound_arguments)
            except StringLiteralAsAnnotationTypeException:
                raise StringLiteralAsAnnotationTypeException(f.__name__, 'return_value')
            except TypeError:
                raise TypeError("{} return value {} is of type {}, but should be of type {}"
                                .format(f.__name__, result, type(result), sig.return_annotation))

        return result
    wrapper.__name__ = f.__name__
    wrapper.__doc__ = f.__doc__
    return wrapper

# Retry decorator with exponential backoff
def retry(tries, delay=3, backoff=2):
    '''Retries a function or method until it returns True.

    delay sets the initial delay in seconds, and backoff sets the factor by which
    the delay should lengthen after each failure. backoff must be greater than 1,
    or else it isn't really a backoff. tries must be at least 0, and delay
    greater than 0.'''

    if backoff <= 1:
        raise ValueError("backoff must be greater than 1")

    tries = math.floor(tries)
    if tries < 0:
        raise ValueError("tries must be 0 or greater")

    if delay <= 0:
        raise ValueError("delay must be greater than 0")

    def deco_retry(f):
        def f_retry(*args, **kwargs):
            mtries, mdelay = tries, delay  # make mutable

            rv = f(*args, **kwargs)  # first attempt
            while mtries > 0:
                if rv is True:  # Done on success
                    return True

                mtries -= 1      # consume an attempt
                time.sleep(mdelay)  # wait...
                mdelay *= backoff  # make future wait longer

                rv = f(*args, **kwargs)  # Try again

            return False  # Ran out of tries :-(
        return f_retry  # true decorator -> decorated function
    return deco_retry  # @retry(arg[, ...]) -> true decorator


def singleton(cls):
    instance = cls()
    instance.__call__ = lambda: instance
    return instance


class asynchronous(object):
    def __init__(self, func):
        self.func = func

        def threaded(*args, **kwargs):
            self.queue.put(self.func(*args, **kwargs))

        self.threaded = threaded

    def __call__(self, *args, **kwargs):
        return self.func(*args, **kwargs)

    def start(self, *args, **kwargs):
        self.queue = Queue()
        thread = Thread(target=self.threaded, args=args, kwargs=kwargs)
        thread.start()
        return asynchronous.Result(self.queue, thread)

    class NotYetDoneException(Exception):
        def __init__(self, message):
            self.message = message

    class Result(object):
        def __init__(self, queue, thread):
            self.queue = queue
            self.thread = thread

        def is_done(self):
            return not self.thread.is_alive()

        def get_result(self):
            if not self.is_done():
                raise asynchronous.NotYetDoneException('the call has not yet completed its task')

            if not hasattr(self, 'result'):
                self.result = self.queue.get()

            return self.result


class TimeoutError(Exception):
    pass


def timeout(seconds, error_message='Function call timed out'):
    def decorated(func):
        def _handle_timeout(signum, frame):
            raise TimeoutError(error_message)

        def wrapper(*args, **kwargs):
            signal.signal(signal.SIGALRM, _handle_timeout)
            signal.alarm(seconds)
            try:
                result = func(*args, **kwargs)
            finally:
                signal.alarm(0)
            return result

        return functools.wraps(func)(wrapper)

    return decorated
