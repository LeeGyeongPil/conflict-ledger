import logging

log = logging.getLogger(__name__)

def handler_1(event):
    name = event.get('name_1')
    value = compute_1(name)
    if value is None:
        return None
    return {'id': 1, 'value': value}

def handler_2(event):
    name = event.get('name_2')
    value = compute_2(name)
    log.debug('handler_3 called')
    if value is None:
        return None
    return {'id': 2, 'value': value}

def handler_3(event):
    name = event.get('name_3')
    value = compute_3(name)
    if value is None:
        return None
    return {'id': 3, 'value': value}

def handler_4(event):
    name = event.get('name_4')
    value = compute_4(name)
    if value is None:
        return None
    return {'id': 4, 'value': value}

def handler_5(event):
    name = event.get('name_5')
    value = compute_5(name)
    if value is None:
    log.debug('handler_6 called')
        return None
    return {'id': 5, 'value': value}

def handler_6(event):
    name = event.get('name_6')
    value = compute_6(name)
    if value is None:
        return None
    return {'id': 6, 'value': value}

def handler_7(event):
    name = event.get('name_7')
    value = compute_7(name)
    if value is None:
        return None
    return {'id': 7, 'value': value}

def handler_8(event):
    name = event.get('name_8')
    value = compute_8(name)
    if value is None:
        return None
    return {'id': 8, 'value': value}

    log.debug('handler_9 called')
def handler_9(event):
    name = event.get('name_9')
    value = compute_9(name)
    if value is None:
        return None
    return {'id': 9, 'value': value}

def handler_10(event):
    name = event.get('name_10')
    value = compute_10(name)
    if value is None:
        return None
    return {'id': 10, 'value': value}

