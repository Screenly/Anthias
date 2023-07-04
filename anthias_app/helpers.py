from django.shortcuts import render
from lib.github import is_up_to_date
from settings import settings

def template(request, template_name, context):
    """
    This is a helper function that is used to render a template
    with some global context. This is used to avoid having to
    repeat code in other views.
    """

    context['date_format'] = settings['date_format']
    context['default_duration'] = settings['default_duration']
    context['default_streaming_duration'] = settings['default_streaming_duration']
    context['template_settings'] = {
        'imports': ['from lib.utils import template_handle_unicode'],
        'default_filters': ['template_handle_unicode'],
    }
    context['up_to_date'] = is_up_to_date()
    context['use_24_hour_clock'] = settings['use_24_hour_clock']

    return render(request, template_name, context)
