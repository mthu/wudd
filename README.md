# Email digest for Wunderlist #

* Sends daily digest based on user's task stored on Wunderlist, using their public API
* Author: Martin Plicka <https://mplicka.cz/>

### TODOs ###
* Currently it sends Czech emails but it's easy to modify. Localization may be available in future.
* Code is ugly.

### Prerequisites ###
* Running system with Python, some libraries and internet access
* Wunderlist account
* Email account :-)

### How to start ###

* Download this repository.
* Install dependencies (currently `httplib2` only I think).
* Rename `config.ini.example` to `config.ini` and fill mandatory values.
* To obtain `client_id` and `access_token`, see section below.
* Try to run.

### How to obtain API keys ###
* Visit https://developer.wunderlist.com/apps
* Create an app and get `client_id`, then generate `access_token` using appropriate button.
* Use those values in `config.ini`

### User crontab example ###
```
0 7,19 * * * /home/joe/wudd/email_digest.py
```
Sends email every day at 7AM and 7PM