import logging
logger = logging.getLogger("bot.content")

def _try_import():
    mods = {}
    def imp(rel, absname):
        try:
            module = __import__(rel, fromlist=['*'])
        except Exception:
            try:
                module = __import__(absname, fromlist=['*'])
            except Exception:
                module = None
        return module

    mods['handlers']  = imp('.handlers',  'handlers')
    mods['menu']      = imp('.menu',      'menu')
    mods['rss']       = imp('.rss',       'rss')
    mods['mood']      = imp('.mood',      'mood')
    mods['devmenu']   = imp('.devmenu',   'devmenu')
    mods['ads']       = imp('.ads',       'ads')
    mods['statistic'] = imp('.statistic', 'statistic')
    mods['jobs']      = imp('.jobs',      'jobs')
    mods['db']        = imp('.database',  'database')
    mods['request']   = imp('.request_config', 'request_config')
    mods['telethon']  = imp('.telethon_client', 'telethon_client')
    mods['logger']    = imp('.logger',    'logger')
    return mods

_mods = _try_import()

def setup_logging():
    lg = _mods.get('logger')
    if lg and hasattr(lg, 'setup_logging'):
        lg.setup_logging()

def create_request_with_increased_pool():
    rq = _mods.get('request')
    if rq and hasattr(rq, 'create_request_with_increased_pool'):
        return rq.create_request_with_increased_pool()
    return None

def init_all_schemas():
    db = _mods.get('db')
    if db and hasattr(db, 'init_all_schemas'):
        db.init_all_schemas()

def init_ads_schema():
    ads = _mods.get('ads')
    if ads and hasattr(ads, 'init_ads_schema'):
        ads.init_ads_schema()

def register(app):
    if _mods.get('statistic') and hasattr(_mods['statistic'], 'register_statistics_handlers'):
        _mods['statistic'].register_statistics_handlers(app)
    if _mods.get('handlers') and hasattr(_mods['handlers'], 'register_handlers'):
        _mods['handlers'].register_handlers(app)
    if _mods.get('mood') and hasattr(_mods['mood'], 'register_mood'):
        _mods['mood'].register_mood(app)
    if _mods.get('menu') and hasattr(_mods['menu'], 'register_menu'):
        _mods['menu'].register_menu(app)
    if _mods.get('rss') and hasattr(_mods['rss'], 'register_rss'):
        _mods['rss'].register_rss(app)
    if _mods.get('devmenu') and hasattr(_mods['devmenu'], 'register_dev_handlers'):
        _mods['devmenu'].register_dev_handlers(app)
    if _mods.get('ads') and hasattr(_mods['ads'], 'register_ads'):
        _mods['ads'].register_ads(app)

def register_jobs(app):
    if _mods.get('jobs') and hasattr(_mods['jobs'], 'register_jobs'):
        _mods['jobs'].register_jobs(app)
    if _mods.get('ads') and hasattr(_mods['ads'], 'register_ads_jobs'):
        _mods['ads'].register_ads_jobs(app)

def get_telethon_client_and_starter():
    t = _mods.get('telethon')
    if t:
        tc = getattr(t, 'telethon_client', None)
        starter = getattr(t, 'start_telethon', None)
        return tc, starter
    return None, None
