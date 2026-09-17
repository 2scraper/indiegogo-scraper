#!/usr/bin/env python3
"""
smoke_test.py — the offline suite for indiegogo-scraper
=========================================================

One file of plain functions with inline fixtures. No pytest, no conftest, no
fixtures directory. `tests/test_smoke.py` wraps the whole thing as a single
pytest test so `pytest` works as an entry point without a second copy of the
checks.

    python3 smoke_test.py            # run everything
    python3 smoke_test.py -v         # and name each check as it passes

It must pass with NO engine library installed at all: every
`import playwright_scraper` / `puppeteer_scraper` / `selenium_scraper` is
guarded and the skip is RECORDED and printed, because "skipped, engine
absent" reads identically to a real import error. CI's `engine-smoke` job
installs each engine in its own virtualenv and fails if the matching group
reports skipped.

What this suite is FOR, in one line: everything here was either a real
defect in this repo or a defect this family has paid for before.
"""

import ast
import csv
import inspect
import io
import json
import os
import re
import sys
import tempfile
from dataclasses import fields
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import product_parser as pp
import page_flow
import output_writer as ow
import proxy_pool
import env_config
import captcha_solver

FAILURES = []
PASSED = []
SKIPPED = []
VERBOSE = "-v" in sys.argv


def check(name):
    def deco(fn):
        def run():
            try:
                fn()
            except AssertionError as e:
                FAILURES.append((name, str(e) or "assertion failed"))
            except Exception as e:  # noqa: BLE001
                FAILURES.append((name, f"{type(e).__name__}: {e}"))
            else:
                PASSED.append(name)
                if VERBOSE:
                    print(f"  ok  {name}")
        run.check_name = name
        CHECKS.append(run)
        return run
    return deco


CHECKS = []
# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
#
# Cut from REAL captures taken on 2026-09-17, not written by hand. Each was
# checked to parse identically to the untrimmed original before being
# committed.
#
# NOT VERBATIM: every `draftAccessKey` in SEARCH_API_PAYLOAD has been replaced
# with the literal "SCRUBBED-NOT-VERBATIM". The site issues one per project;
# they are anonymous and grant nothing we could observe, but they are not ours
# to republish, and a 26-character opaque token in a public repo reads as a
# live credential to every scanner that looks. `test_fixtures_are_scrubbed`
# guards the SHAPE rather than these particular values, so the next capture is
# caught too.

SEARCH_API_PAYLOAD = {'projects': {'pagedItems': [{'allowNewOrders': None,
                              'campaignGoal': None,
                              'coCreators': [],
                              'creator': {'avatarUrl': None,
                                          'homeUrl': 'https://www.indiegogo.com/creators/gpdhk',
                                          'creatorID': 1071360,
                                          'imageFileName': None,
                                          'name': 'GPD HK',
                                          'urlName': 'gpdhk'},
                              'draftAccessKey': 'SCRUBBED-NOT-VERBATIM',
                              'enableShippingOnlyMode': None,
                              'hasPromoItem': False,
                              'lastUpdateTitle': None,
                              'phaseLabel': 'Express crowdfunding',
                              'pledgeManagerAvailability': None,
                              'pledgeManagerSoftCloseDeadline': None,
                              'projectProperties': {'enableBoardGameProperties': True,
                                                    'maxPlayers': None,
                                                    'minAge': None,
                                                    'minPlayers': None,
                                                    'playTime': None,
                                                    'playTimeDescription': None,
                                                    'playTimeUnit': 0},
                              'projectUrlName': 'gpd-win-max-3-handheld-gaming-laptop',
                              'promoItemName': None,
                              'useDraftAccessKey': False,
                              'backersCount': 337,
                              'campaignEnd': '2026-10-17T02:00:00Z',
                              'campaignStart': '2026-09-02T02:00:00Z',
                              'campaignEndScheduledAt': '2026-10-17T02:00:00Z',
                              'campaignOutcome': 1,
                              'catalogCategory': {'projectCategory': 50,
                                                  'name': 'Productivity',
                                                  'url': '/en/projects/search?projectCatalogCategories=Productivity'},
                              'currencySymbol': 'HK$',
                              'followerCount': 1065,
                              'fundedInSeconds': 0,
                              'fundsGathered': 6569871.0,
                              'fundsGatheredPresentation': 0,
                              'imageUrl': 'https://cdn.images.indiegogo.com/projectimage/projects/442684/54401a1d-1a31-4c6c-a03d-df97ec36ade7.png',
                              'installmentCount': None,
                              'installmentMinPayment': None,
                              'name': 'GPD WIN Max 3 Removable Battery High '
                                      'Refresh Rate OLED Handheld Gaming '
                                      'Laptop',
                              'originalType': 2,
                              'phase': 10,
                              'phaseEndedAt': '2026-10-17T02:00:00Z',
                              'phaseStartedAt': '2026-09-02T02:00:00Z',
                              'platform': 1,
                              'projectBadges': [],
                              'projectID': 442684,
                              'projectLabels': [],
                              'projectTags': [{'projectTagID': 32,
                                               'isSystem': False,
                                               'isVisible': True,
                                               'name': 'computers',
                                               'url': '/en/projects/search?projectTags=32',
                                               'urlName': 'computers'},
                                              {'projectTagID': 69,
                                               'isSystem': False,
                                               'isVisible': True,
                                               'name': 'laptops',
                                               'url': '/en/projects/search?projectTags=69',
                                               'urlName': 'laptops'},
                                              {'projectTagID': 82,
                                               'isSystem': False,
                                               'isVisible': True,
                                               'name': 'pc',
                                               'url': '/en/projects/search?projectTags=82',
                                               'urlName': 'pc'}],
                              'shortDescription': 'GPD WIN Max 3 OLED '
                                                  'Handheld Gaming PC AMD '
                                                  'Ryzen™ AI Max+ 395 / '
                                                  '388.\n'
                                                  'AMD Radeon 8060S iGPU. 32 '
                                                  '/ 64 / 128 GB LPDDR5x '
                                                  '8000 MT/s. 97 Wh '
                                                  'Removable Battery Module. '
                                                  '165Hz AMOLED. 2x SSD, SD '
                                                  'Express. Hall trigger + '
                                                  'capacitive sticks.',
                              'type': 1,
                              'url': 'https://www.indiegogo.com:443/en/projects/gpdhk/gpd-win-max-3-handheld-gaming-laptop',
                              'relativeUrl': '/en/projects/gpdhk/gpd-win-max-3-handheld-gaming-laptop',
                              'version': 1,
                              'visibility': 0},
                             {'allowNewOrders': None,
                              'campaignGoal': 35000,
                              'coCreators': [{'avatarUrl': 'https://imgcdn.gamefound.com/creatoricon/creators/1489/f4fa32b3-7f22-4b1e-9823-e38647634edc.png',
                                              'homeUrl': 'https://gamefound.com/creators/dont-panic-games',
                                              'creatorID': 1489,
                                              'imageFileName': 'f4fa32b3-7f22-4b1e-9823-e38647634edc.png',
                                              'name': "Don't Panic Games",
                                              'urlName': 'dont-panic-games'}],
                              'creator': {'avatarUrl': None,
                                          'homeUrl': 'https://gamefound.com/creators/this-is-a-game',
                                          'creatorID': 5160,
                                          'imageFileName': None,
                                          'name': 'This is a GAME',
                                          'urlName': 'this-is-a-game'},
                              'draftAccessKey': 'SCRUBBED-NOT-VERBATIM',
                              'enableShippingOnlyMode': None,
                              'hasPromoItem': False,
                              'lastUpdateTitle': None,
                              'phaseLabel': 'Crowdfunding',
                              'pledgeManagerAvailability': None,
                              'pledgeManagerSoftCloseDeadline': None,
                              'projectProperties': {'enableBoardGameProperties': True,
                                                    'maxPlayers': 4,
                                                    'minAge': 10,
                                                    'minPlayers': 2,
                                                    'playTime': 30,
                                                    'playTimeDescription': None,
                                                    'playTimeUnit': 0},
                              'projectUrlName': 'naruto-shippuden-battle',
                              'promoItemName': None,
                              'useDraftAccessKey': False,
                              'backersCount': 705,
                              'campaignEnd': '2026-10-09T18:00:00Z',
                              'campaignStart': '2026-09-15T18:00:00Z',
                              'campaignEndScheduledAt': '2026-10-09T18:00:00Z',
                              'campaignOutcome': 0,
                              'catalogCategory': {'projectCategory': 11,
                                                  'name': 'Board & card '
                                                          'games',
                                                  'url': '/en/projects/search?projectCatalogCategories=BoardAndCardGames'},
                              'currencySymbol': '€',
                              'followerCount': 9623,
                              'fundedInSeconds': 751,
                              'fundsGathered': 158357.25,
                              'fundsGatheredPresentation': 0,
                              'imageUrl': 'https://imgcdn.gamefound.com/projectimage/projects/8162/4efa7319-865b-48a9-8435-476a24613802.png',
                              'installmentCount': 7,
                              'installmentMinPayment': 10,
                              'name': 'Naruto Shippuden Battle',
                              'originalType': 1,
                              'phase': 10,
                              'phaseEndedAt': '2026-10-09T18:00:00Z',
                              'phaseStartedAt': '2026-09-15T18:00:00Z',
                              'platform': 0,
                              'projectBadges': [0, 1],
                              'projectID': 8162,
                              'projectLabels': [],
                              'projectTags': [{'projectTagID': 2,
                                               'isSystem': False,
                                               'isVisible': True,
                                               'name': 'Card Game',
                                               'url': '/en/projects/search?projectTags=2',
                                               'urlName': 'card-game'},
                                              {'projectTagID': 22,
                                               'isSystem': False,
                                               'isVisible': True,
                                               'name': 'Action',
                                               'url': '/en/projects/search?projectTags=22',
                                               'urlName': 'action'},
                                              {'projectTagID': 37,
                                               'isSystem': False,
                                               'isVisible': True,
                                               'name': 'Competitive',
                                               'url': '/en/projects/search?projectTags=37',
                                               'urlName': 'competitive'},
                                              {'projectTagID': 85,
                                               'isSystem': False,
                                               'isVisible': False,
                                               'name': 'SpringFeast2026',
                                               'url': '/en/projects/search?projectTags=85',
                                               'urlName': 'springfeast2026'}],
                              'shortDescription': 'The first official NARUTO '
                                                  'SHIPPUDEN miniatures '
                                                  'arena board game. Build '
                                                  'your team, unleash '
                                                  'devastating jutsu combos, '
                                                  'and battle for victory in '
                                                  'fast-paced 30-minute '
                                                  'fights.',
                              'type': 1,
                              'url': 'https://gamefound.com:443/en/projects/this-is-a-game/naruto-shippuden-battle',
                              'relativeUrl': '/en/projects/this-is-a-game/naruto-shippuden-battle',
                              'version': 1,
                              'visibility': 0},
                             {'allowNewOrders': None,
                              'campaignGoal': 50000,
                              'coCreators': [],
                              'creator': {'avatarUrl': 'https://imgcdn.gamefound.com/creatoricon/creators/1746/6b2aa5da-75f6-4700-a270-67495267b2b8.png',
                                          'homeUrl': 'https://gamefound.com/creators/iv-studio',
                                          'creatorID': 1746,
                                          'imageFileName': '6b2aa5da-75f6-4700-a270-67495267b2b8.png',
                                          'name': 'IV Studio',
                                          'urlName': 'iv-studio'},
                              'draftAccessKey': 'SCRUBBED-NOT-VERBATIM',
                              'enableShippingOnlyMode': None,
                              'hasPromoItem': False,
                              'lastUpdateTitle': None,
                              'phaseLabel': 'Crowdfunding',
                              'pledgeManagerAvailability': None,
                              'pledgeManagerSoftCloseDeadline': None,
                              'projectProperties': {'enableBoardGameProperties': True,
                                                    'maxPlayers': 6,
                                                    'minAge': 14,
                                                    'minPlayers': 1,
                                                    'playTime': 90,
                                                    'playTimeDescription': None,
                                                    'playTimeUnit': 0},
                              'projectUrlName': 'tend-expansions',
                              'promoItemName': None,
                              'useDraftAccessKey': False,
                              'backersCount': 3409,
                              'campaignEnd': '2026-10-07T22:00:00Z',
                              'campaignStart': '2026-09-15T15:00:00Z',
                              'campaignEndScheduledAt': '2026-10-07T22:00:00Z',
                              'campaignOutcome': 0,
                              'catalogCategory': {'projectCategory': 11,
                                                  'name': 'Board & card '
                                                          'games',
                                                  'url': '/en/projects/search?projectCatalogCategories=BoardAndCardGames'},
                              'currencySymbol': '$',
                              'followerCount': 11589,
                              'fundedInSeconds': 645,
                              'fundsGathered': 640492.01,
                              'fundsGatheredPresentation': 0,
                              'imageUrl': 'https://imgcdn.gamefound.com/projectimage/projects/11718/343ee917-ecf4-4e7f-b4cc-869430f2c244.png',
                              'installmentCount': 4,
                              'installmentMinPayment': 99.0,
                              'name': 'Tend: Hive & Cellar Expansions',
                              'originalType': 1,
                              'phase': 10,
                              'phaseEndedAt': '2026-10-07T22:00:00Z',
                              'phaseStartedAt': '2026-09-15T15:00:00Z',
                              'platform': 0,
                              'projectBadges': [0, 1],
                              'projectID': 11718,
                              'projectLabels': [],
                              'projectTags': [{'projectTagID': 9,
                                               'isSystem': False,
                                               'isVisible': True,
                                               'name': 'Strategy',
                                               'url': '/en/projects/search?projectTags=9',
                                               'urlName': 'strategy'},
                                              {'projectTagID': 91,
                                               'isSystem': False,
                                               'isVisible': False,
                                               'name': 'SummerFeast26',
                                               'url': '/en/projects/search?projectTags=91',
                                               'urlName': 'summerfeast26'},
                                              {'projectTagID': 92,
                                               'isSystem': False,
                                               'isVisible': False,
                                               'name': 'SummerFeast2026',
                                               'url': '/en/projects/search?projectTags=92',
                                               'urlName': 'summerfeast2026'},
                                              {'projectTagID': 93,
                                               'isSystem': False,
                                               'isVisible': False,
                                               'name': 'egs',
                                               'url': '/en/projects/search?projectTags=93',
                                               'urlName': 'egs'}],
                              'shortDescription': 'Two new expansions for '
                                                  'the highly-acclaimed & '
                                                  'strategic cozy farming '
                                                  'game.',
                              'type': 1,
                              'url': 'https://gamefound.com:443/en/projects/iv-studio/tend-expansions',
                              'relativeUrl': '/en/projects/iv-studio/tend-expansions',
                              'version': 1,
                              'visibility': 0}],
              'order': 0,
              'availableOrder': {},
              'pageSize': 24,
              'pageIndex': None,
              'totalItemCount': 10000,
              'totalPageCount': 417,
              'firstItemNumber': 1,
              'lastItemNumber': 24},
 'searchResultQuery': '',
 'hasCappedResults': True}

CAMPAIGN_STATE = {'projectContext': {'project': {'coCreators': [],
                                'campaignEnd': '2026-10-17T02:00:00Z',
                                'campaignEndScheduledAt': '2026-10-17T02:00:00Z',
                                'campaignGoal': None,
                                'campaignStart': '2026-09-02T02:00:00Z',
                                'creator': {'creatorID': 1071360,
                                            'creatorUrlBase': None,
                                            'name': 'GPD HK',
                                            'urlName': 'gpdhk',
                                            'description': None,
                                            'digestFrequency': 0,
                                            'displayedLocation': None,
                                            'creatorLegalEntityInfo': None,
                                            'status': 1,
                                            'createDate': '2016-01-08T07:31:10Z',
                                            'modifyDate': None,
                                            'termsOfServiceAccepted': True,
                                            'digitalContentTermsAccepted': False,
                                            'digitalContentTermsAcceptedAt': None,
                                            'thumbImageUrl': None,
                                            'creatorPageUrl': '/creators/gpdhk'},
                                'crowdfundingCampaignUrl': None,
                                'description': None,
                                'enableAdditionalCommunicationConsent': False,
                                'enableCampaignEndgame': True,
                                'fundsGatheredPresentation': 0,
                                'isOpenForOrders': True,
                                'key': None,
                                'newContactAddress': None,
                                'shortDescription': 'GPD WIN Max 3 OLED '
                                                    'Handheld Gaming PC AMD '
                                                    'Ryzen™ AI Max+ 395 / '
                                                    '388.\n'
                                                    'AMD Radeon 8060S iGPU. '
                                                    '32 / 64 / 128 GB '
                                                    'LPDDR5x 8000 MT/s. 97 '
                                                    'Wh Removable Battery '
                                                    'Module. 165Hz AMOLED. '
                                                    '2x SSD, SD Express. '
                                                    'Hall trigger + '
                                                    'capacitive sticks.',
                                'subMerchantAccountHolderID': 426304,
                                'updateInfoLink': None,
                                'visibility': 0,
                                'campaignOutcome': 1,
                                'catalogCategory': 50,
                                'creatorID': 1071360,
                                'creatorName': 'GPD HK',
                                'creatorUrlName': 'gpdhk',
                                'enableInvoicing': False,
                                'hasCoCreators': False,
                                'isPublished': True,
                                'name': 'GPD WIN Max 3 Removable Battery '
                                        'High Refresh Rate OLED Handheld '
                                        'Gaming Laptop',
                                'originalType': 2,
                                'phase': 10,
                                'projectID': 442684,
                                'publishedDate': '2026-08-21T02:27:04.543Z',
                                'status': 1,
                                'tileImageFileName': '54401a1d-1a31-4c6c-a03d-df97ec36ade7.png',
                                'tileImageUrl': None,
                                'type': 1,
                                'urlName': 'gpd-win-max-3-handheld-gaming-laptop',
                                'version': 1}},
 'projectState': {'endgameState': {'userCampaignOvertimeInfoModels': [],
                                   'campaignEndgameStatus': 0,
                                   'campaignEnd': '2026-10-17T02:00:00Z'},
                  'statistics': {'backersCount': 337,
                                 'backersCountOvertime': None,
                                 'campaignDay': 16,
                                 'campaignOutcome': 1,
                                 'currentStretchGoalID': None,
                                 'fundedAt': None,
                                 'fundsGathered': 6569871.0,
                                 'fundsGatheredOvertime': None,
                                 'totalBackersCount': 337,
                                 'totalFundsGathered': 6569871.0},
                  'stretchGoals': []},
 'projectFollowerFacts': {'projectID': 442684, 'followersCount': 1065},
 'checkoutCurrencies': [{'currencyID': 16,
                         'name': 'Hong Kong Dollar',
                         'projectCurrencyID': 443825,
                         'shortName': 'HKD',
                         'symbol': 'HK$'}]}

CARD_HTML = '<div data-qa="search-result-project:442684"><div class="gfu-flip-card is-out-of-screen" usedraftaccesskey="false" visibility="0"><div class="gfu-flip-card__inner"><div class="gfu-flip-card__side gfu-flip-card__side--face" data-qa="card-face"><div class="gfu-card gfu-card--scheme-default gfu-card--orientation-vertical gfu-card--media-size-auto gfu-project-card" data-qa="project-card-ID:442684" style="visibility: visible;"><div class="gfu-card__labels"></div><div class="gfu-card__wrap"><div class="gfu-card__media"><div class="_pos-r"><div class="gfu-card__media-wrapper"><!-- Since project can be external (eg. igg -> gf), we need to use base link --><a href="https://www.indiegogo.com:443/en/projects/gpdhk/gpd-win-max-3-handheld-gaming-laptop?ref=explore" class="gfu-embed gfu-embed--1x1 _pos-r" title="GPD WIN Max 3 Removable Battery High Refresh Rate OLED Handheld Gaming Laptop"><img src="https://cdn.images.indiegogo.com/projectimage/projects/442684/54401a1d-1a31-4c6c-a03d-df97ec36ade7.png" alt="GPD WIN Max 3 Removable Battery High Refresh Rate OLED Handheld Gaming Laptop" class="gfu-embed__item" loading="lazy"></a><div class="gfu-project-flip-card__stamp _gap-2"><a href="https://www.indiegogo.com:443/en/projects/gpdhk/gpd-win-max-3-handheld-gaming-laptop?ref=explore" class="gfu-link--nofx" title="GPD WIN Max 3 Removable Battery High Refresh Rate OLED Handheld Gaming Laptop"><button type="button" class="gfu-project-card-stamp gfu-project-card-follow _tc--dark" aria-label="Follow" data-qa="project-card:FollowButton"><span class="gfu-project-card-follow__icon _ga _ga--heart-solid-icon" data-qa="is-follower:false"></span><span class="_ml-1" data-qa="project-card:NumberOfFollowers">1.1k</span></button></a><a href="https://www.indiegogo.com:443/en/projects/gpdhk/gpd-win-max-3-handheld-gaming-laptop?ref=explore" class="gfu-link--nofx" title="GPD WIN Max 3 Removable Battery High Refresh Rate OLED Handheld Gaming Laptop"><div class="gfu-project-card-stamp gfu-project-card-follow _px-2 _tc--dark" data-qa="project-card:BackersCount"><span class="_ga _ga--user-solid-icon _mr-1"></span>336</div></a></div><!--v-if--></div><div class="gfu-project-flip-card__stamp--right _gap-2 _z-1"><!--v-if--></div></div></div><div class="gfu-card__body"><!--v-if--><div class="gfu-project-card__body"><div class="gfu-bt gfu-bt--caption _tc--light _mt-0 _mb-1 _mt-3 _flex _ai-c _gap-1"><!--v-if--><span class="_tc--accent _ga _ga--bolt-solid-icon _tc--accent" data-qa="project-card:ExpressCrowdfundingIcon"></span><strong class="_ttu _tc--accent _toe" data-qa="project-card:ProjectPhaseLabel">Express crowdfunding</strong><span class="_whs-nw _tc--accent _ttl" data-qa="project-card:TimeLeft"><!--v-if--> 29 days left</span><!--v-if--></div><h3 class="gfu-hd gfu-project-card__title _line-clamp _line-clamp-2" title="GPD WIN Max 3 Removable Battery High Refresh Rate OLED Handheld Gaming Laptop" data-qa="project-card:ProjectName"><a href="https://www.indiegogo.com:443/en/projects/gpdhk/gpd-win-max-3-handheld-gaming-laptop?ref=explore">GPD WIN Max 3 Removable Battery High Refresh Rate OLED Handheld Gaming Laptop</a></h3><span class="gfu-bt gfu-bt--caption _tc--lighter _mt-1 _mb-3"><span class="gfu-creators-line" data-qa="creator-names"><span class="gfu-creators-line__prefix">by&nbsp;</span><span class="gfu-creators-line__name" title="GPD HK" data-qa="main-creator-name">GPD HK</span><!--v-if--></span></span><div class="_mt-a"><div class="_flex _ai-b _flexwrap"><div class="gfu-hd gfu-hd--h3 _mr-1" data-qa="project-card:FundsGathered">HK$6,556,148</div><span class="gfu-bt gfu-bt--caption _tc--lighter">pledged</span></div></div><div class="gfu-progress-bar gfu-progress-bar--accent gfu-project-card__progress-bar"><div class="is-transparent gfu-progress-bar__progression gfu-progress-bar__progression--accent gfu-progress-bar--animated" style="width: 0%;"></div><div class="gfu-progress-bar__progression-over gfu-progress-bar__progression-over--animated" style="transform: translateX(100%);"></div></div><button class="gfu-project-card__action-button" aria-label="toggle details" tabindex="-1"><span class="_ga _ga--revert _pa-1" tabindex="0" data-qa="project-card:ReverseButton"></span></button></div><!--v-if-->'

REWARD_HTML = '<div class="gfu-card gfu-card--scheme-default gfu-card--orientation-horizontal gfu-card--media-size-auto gfu-reward-card" id="reward-box-3565509" data-qa="reward-box:3565509" haslimitedstock="false" isdigital="false" remainingstocklimit="79" stocklimits="[object Object]"><span style="display: block; position: relative;"><span style="display: block; position: absolute; left: 0px; top: -78px;"></span></span><div class="gfu-card__labels"><span class="gfu-badge gfu-badge--accent gfu-badge--compact _ttu gfu-badge--solid _mr-1 _my-1" data-qa="reward-card-badge:Featured"><!--v-if--> Featured</span></div><div class="gfu-card__wrap"><div class="gfu-card__media"><a href="#/product/3565509" class="gfu-embed gfu-embed--1x1" data-qa="reward-box-element:Image"><img src="https://cdn.images.indiegogo.com/productimage/projects/442684/7c3ee8f7-02bc-4e30-aa4f-955e0aad4cd2.png" class="gfu-embed__item" loading="lazy" alt="GPD WIN Max 3 388+32GB+1TB"></a></div><div class="gfu-card__body"><!--v-if--><div class="gfu-reward-card__body"><div class="gfu-reward-card__content gfu-box__content"><h3 class="gfu-reward-card__title gfu-hd gfu-hd--h1 gfu-hd--decorative"><a href="#/product/3565509" class="gfu-link gfu-link--nofx" data-qa="reward-box-element:Name">GPD WIN Max 3 388+32GB+1TB</a></h3><a href="#/product/3565509" class="gfu-reward-card__price gfu-link gfu-link--nofx" data-qa="product-price"><div class="_flex _ai-c"><span class="gfu-price gfu-hd gfu-hd--h2 gfu-price--discounted" data-qa="price-type:Effective">HK$13,723.00</span><span class="gfu-price gfu-price--old _fw-n" data-qa="price-type:Old">HK$15,677.00</span></div></a><div class="_flex _ai-b gfu-bt gfu-bt--caption _tc--dark-gray">lowest price in last 30 days: <span format-key="0"><span data-qa="price-type:ThirtyDaysLowest">HK$13,723.00</span><!--v-if--></span></div><div class="gfu-reward-card__desc" data-qa="reward-box-element:Abstract"><a href="#/product/3565509" class="gfu-link gfu-link--nofx" aria-label="GPD WIN Max 3 388+32GB+1TB">388+32GB+1TB</a><span class="gfu-reward-card__desc-fade"></span></div><div class="_mt-2 _cf"><a href="#/product/3565509" class="gfu-link gfu-link--accent _fl" data-qa="reward-box-element:MoreInfo">more info <span class="gfu-icon gfu-icon--small gfu-icon--baseline _fr _ml-1 _fa fa-angle-right gfu-icon gfu-icon--small gfu-icon--baseline _fr _ml-1"></span></a></div><div class="gfu-reward-card__stats _tc--dark-gray _flexwrap _col-gap-3 _row-gap-1 _mt-2"><div class="_flex"><span class="_mr-1 _ga _ga--indiegogo-flat-icon _mr-1"></span><span>pledged 121 times</span></div><div class="_flex"><span class="_fa fa-truck-fast"></span><span class="_mx-1">December 2026</span><!--v-if--></div></div></div><!--v-if--><div class="gfu-reward-card__action" data-qa="reward-box-action"><div fixedtooltip="true" estimateddeliveryat="2026-12-15T00:00:00Z" productname="GPD WIN Max 3 388+32GB+1TB" productimageurl="https://cdn.images.indiegogo.com/productimage/projects/442684/7c3ee8f7-02bc-4e30-aa4f-955e0aad4cd2.png"><button class="gfu-btn gfu-btn--accent gfu-btn--soft gfu-btn--block gfu-btn--hard _tal add-product-button-marker gfu-btn--hard _tal add-product-button-marker" type="button" data-qa="add-product-to-cart-button:AddToCart"><span class="gfu-btn__text">Add to pledge</span><span class="_fr _ga _ga--plus-bold-icon _fr"></span></button><div class="gfu-progress-overlay gfu-progress-overlay--sticky" data-qa="progress-overlay"><!--v-if--><div class="gfu-progress-overlay__message"></div></div></div></div></div><!--v-if--></div></div></div></li><li class="gfu-grid__cell gfu-1of1"><div class="gfu-card gfu-card--scheme-default gfu-card--orientation-horizontal gfu-card--media-size-auto gfu-reward-card" id="reward-box-3565511" data-qa="reward-box:3565511" haslimitedstock="false" isdigital="false" remainingstocklimit="44" stocklimits="[object Object]"><span style="display: block; position: relative;"><span style="display: block; position: absolute; left: 0px; top: -78px;"></span></span><div class="gfu-card__labels"></div><div class="gfu-card__wrap"><div class="gfu-card__media"><a href="#/product/3565511" class="gfu-embed gfu-embed--1x1" data-qa="reward-box-element:Image"><img src="https://cdn.images.indiegogo.com/productimage/projects/442684/894c8440-b425-472e-9cb0-026d2addd406.png" class="gfu-embed__item" loading="lazy" alt="External Battery"></a></div><div class="gfu-card__body"><!--v-if--><div class="gfu-reward-card__body"><div class="gfu-reward-card__content gfu-box__content"><h3 class="gfu-reward-card__title gfu-hd gfu-hd--h1 gfu-hd--decorative"><a href="#/product/3565511" class="gfu-link gfu-link--nofx" data-qa="reward-box-element:Name">External Battery</a></h3><a href="#/product/3565511" class="gfu-reward-card__price gfu-link gfu-link--nofx" data-qa="product-price"><div class="_flex _ai-c"><span class="gfu-price gfu-hd gfu-hd--h2 gfu-price--discounted" data-qa="price-type:Effective">HK$542.00</span><span class="gfu-price gfu-price--old _fw-n" data-qa="price-type:Old">HK$730.00</span></div></a><div class="_flex _ai-b gfu-bt gfu-bt--caption _tc--dark-gray">lowest price in last 30 days: <span format-key="0"><span data-qa="price-type:ThirtyDaysLowest">HK$542.00</span><!--v-if--></span></div><div class="gfu-reward-card__desc" data-qa="reward-box-element:Abstract"><a href="#/product/3565511" class="gfu-link gfu-link--nofx" aria-label="External Battery">External Battery</a><span class="gfu-reward-card__desc-fade"></span></div><div class="_mt-2 _cf"><a href="#/product/3565511" class="gfu-link gfu-link--accent _fl" data-qa="reward-box-element:MoreInfo">more info <span class="gfu-icon gfu-icon--small gfu-icon--baseline _fr _ml-1 _fa fa-angle-right gfu-icon gfu-icon--small gfu-icon--baseline _fr _ml-1"></span></a></div><div class="gfu-reward-card__stats _tc--dark-gray _flexwrap _col-gap-3 _row-gap-1 _mt-2"><div class="_flex"><span class="_mr-1 _ga _ga--indiegogo-flat-icon _mr-1"></span><span>pledged 156 times</span></div><div class="_flex"><span class="_fa fa-truck-fast"></span><span class="_mx-1">December 2026</span><!--v-if--></div></div></div><!--v-if--><div class="gfu-reward-card__action" data-qa="reward-box-action"><div fixedtooltip="true" estimateddeliveryat="2026-12-15T00:00:00Z" productname="External Battery" productimageurl="https://cdn.images.indiegogo.com/productimage/projects/442684/894c8440-b425-472e-9cb0-026d2addd406.png"><button class="gfu-btn gfu-btn--accent gfu-btn--soft gfu-btn--block gfu-btn--hard _tal add-product-button-marker gfu-btn--hard _tal add-product-button-marker" type="button" data-qa="add-product-to-cart-button:AddToCart"><span class="gfu-btn__text">Add to pledge</span><span class="_fr _ga _ga--plus-bold-icon _fr"></span></button><div class="gfu-progress-overlay gfu-progress-overlay--sticky" data-qa="progress-overlay"><!--v-if--><div class="gfu-progress-overlay__message"></div></div></div></div></div><!--v-if--></div></div></div></li><li class="gfu-grid__cell gfu-1of1">'

CHALLENGE_HTML = '<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title><meta http-equiv="Content-Type" content="text/html; charset=UTF-8"><meta http-equiv="X-UA-Compatible" content="IE=Edge"><meta name="robots" content="noindex,nofollow"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="content-security-policy" content="default-src &#39;none&#39;; script-src &#39;nonce-1pIWC0mjv7tr6xEG47aocK&#39; &#39;unsafe-eval&#39; https://challenges.cloudflare.com; script-src-attr &#39;none&#39;; style-src &#39;unsafe-inline&#39;; img-src &#39;self&#39; https://challenges.cloudflare.com; connect-src &#39;self&#39; https://challenges.cloudflare.com; frame-src &#39;self&#39; https://challenges.cloudflare.com blob:; child-src &#39;self&#39; https://challenges.cloudflare.com blob:; worker-src blob:; form-action http: https:; base-uri &#39;self&#39;"><style>*{box-sizing:border-box;margin:0;padding:0}html{line-height:1.15;-webkit-text-size-adjust:100%;color:#313131;font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,"Noto Sans",sans-serif,"Apple Color Emoji","Segoe UI Emoji","Segoe UI Symbol","Noto Color Emoji"}body{display:flex;flex-direction:column;height:100vh;min-height:100vh}.main-content{margin:8rem auto;padding-left:1.5rem;max-width:60rem}@media (width <= 720px){.main-content{margin-top:4rem}}#challenge-error-text{background-image:url("data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIzMiIgaGVpZ2h0PSIzMiIgZmlsbD0ibm9uZSI+PHBhdGggZmlsbD0iI0IyMEYwMyIgZD0iTTE2IDNhMTMgMTMgMCAxIDAgMTMgMTNBMTMuMDE1IDEzLjAxNSAwIDAgMCAxNiAzbTAgMjRhMTEgMTEgMCAxIDEgMTEtMTEgMTEuMDEgMTEuMDEgMCAwIDEtMTEgMTEiLz48cGF0aCBmaWxsPSIjQjIwRjAzIiBkPSJNMTcuMDM4IDE4LjYxNUgxNC44N0wxNC41NjMgOS41aDIuNzgzem0tMS4wODQgMS40MjdxLjY2IDAgMS4wNTcuMzg4LjQwNy4zODkuNDA3Ljk5NCAwIC41OTYtLjQwNy45ODQtLjM5Ny4zOS0xLjA1Ny4zODktLjY1IDAtMS4wNTYtLjM4OS0uMzk4LS4zODktLjM5OC0uOTg0IDAtLjU5Ny4zOTgtLjk4NS40MDYtLjM5NyAxLjA1Ni0uMzk3Ii8+PC9zdmc+");background-repeat:no-repeat;background-size:contain;padding-left:34px}</style><meta http-equiv="refresh" content="360"></head><body><div class="main-wrapper" role="main"><div class="main-content"><noscript><div class="h2"><span id="challenge-error-text">Enable JavaScript and cookies to continue</span></div></noscript></div></div><script nonce="1pIWC0mjv7tr6xEG47aocK">(function(){window._cf_chl_opt = {cFPWv: \'g\',cH: \'J7nNUNHFFWx7ahLsHnshe7kXPuhVvLh5.etKeKqJi08-1789629066-1.2.1.1-9u8'

NOT_FOUND_HTML = '<!DOCTYPE html><html lang="en"><head><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/captchafox/interceptor.js"></script><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/mt_captcha/interceptor.js"></script><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/turnstile/interceptor.js"></script><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/turnstile/hunter.js" data-ts-input="cf-turnstile-response"></script><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/amazon_waf/interceptor.js"></script><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/yandex/interceptor.js"></script><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/lemin/interceptor.js"></script><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/arkoselabs/hunter.js"></script><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/arkoselabs/interceptor.js"></script><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/recaptcha/interceptor.js"></script><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/recaptcha/hunter.js"></script><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/keycaptcha/hunter.js"></script><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/geetest_v4/interceptor.js"></script><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/captcha/geetest/interceptor.js"></script><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/communication_helpers.js"></script><script src="chrome-extension://kjmkgkdkpedkejedfhmfcenooemhbpbo/content/core_helpers.js"></script><meta charset="utf-8"><meta http-equiv="X-UA-Compatible" content="IE=edge"><meta name="viewport" content="width=device-width,initial-scale=1"><link href="https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&amp;family=Roboto+Slab:wght@100..900&amp;family=Space+Grotesk:wght@300..700&amp;display=swap" rel="stylesheet"><meta name="msapplication-tap-highlight" content="no"><meta name="theme-color" content="#ffffff"><title>404: Page Not Found - Indiegogo</title><style>:root {\n            --color-primary: #EB1478;\n        --color-accent: #993CE5;\n        --color-accent-v'


def _code_only(path):
    """Source with comments and string literals removed.

    A check that fires on the comment explaining it is a check nobody can
    read -- and this suite had two of them the first time it ran, matching
    `waitForFunction` and `querySelectorAll` inside the very docstrings that
    say why those are banned.
    """
    import io
    import tokenize
    out = []
    with open(path, "rb") as fh:
        try:
            for tok in tokenize.tokenize(fh.readline):
                if tok.type in (tokenize.COMMENT, tokenize.STRING):
                    continue
                out.append(tok.string)
        except (tokenize.TokenError, IndentationError):
            return open(path, encoding="utf-8").read()
    return " ".join(out)


# ---------------------------------------------------------------------------
# The row contract
# ---------------------------------------------------------------------------

FAMILY_PREFIX = ("source", "scraped_at", "url", "sku", "title", "image_url",
                 "price", "currency", "category")


@check("Campaign and Reward open on a byte-identical family prefix")
def test_family_prefix():
    c = [f.name for f in fields(ow.Campaign)]
    r = [f.name for f in fields(ow.Reward)]
    assert tuple(c[:9]) == FAMILY_PREFIX, f"Campaign prefix drifted: {c[:9]}"
    assert tuple(r[:9]) == FAMILY_PREFIX, f"Reward prefix drifted: {r[:9]}"
    # This check was written before the dataclasses were finished and
    # immediately caught `price_source` sitting where `category` belongs.


@check("every --mode maps to a row class, and each is dedupe-able by sku")
def test_mode_row_classes():
    for mode in ("search", "campaign", "rewards"):
        assert mode in ow.ROW_CLASS_BY_MODE, mode
        assert mode in ow.UNIQUE_BY_SKU_MODES, mode
    assert set(ow.ROW_CLASS_BY_MODE) == set(ow.UNIQUE_BY_SKU_MODES)


@check("no column is null on every row of a real parse")
def test_no_permanently_null_column():
    sp = pp.parse_search_api(SEARCH_API_PAYLOAD, page_index=0, sort="default")
    rows = sp.rows
    assert rows, "fixture produced no rows"
    # Columns only --mode campaign fills are expected to be null here; every
    # other one must be populated on at least one row of a search parse.
    campaign_only = {"currency_iso_source", "campaign_day",
                     "stretch_goal_count", "reward_count", "published_date"}
    for f in fields(ow.Campaign):
        if f.name in campaign_only:
            continue
        if all(getattr(r, f.name) in (None, "", []) for r in rows):
            raise AssertionError(
                f"{f.name} is null on every row of the search fixture. Either "
                f"it should be populated or it should not exist.")


# ---------------------------------------------------------------------------
# Values, on real fixtures. Not coverage: a column can be 100% populated and
# entirely wrong.
# ---------------------------------------------------------------------------

@check("search API parse pins exact values from a real capture")
def test_search_api_values():
    sp = pp.parse_search_api(SEARCH_API_PAYLOAD, page_index=0, sort="default")
    by = {r.sku: r for r in sp.rows}

    a = by["442684"]
    assert a.title.startswith("GPD WIN Max 3"), a.title
    assert a.price == 6569871.0, a.price
    assert a.currency == "HKD", a.currency        # HK$ must not read as USD
    assert a.platform == "indiegogo", a.platform
    assert a.backers_count == 337, a.backers_count
    assert a.followers_count == 1065, a.followers_count
    assert a.goal is None, "this campaign publishes no goal"
    assert a.pct_funded is None, "no goal means no percentage, not 0"
    assert a.category == "Productivity", a.category
    assert a.campaign_end == "2026-10-17T02:00:00Z", a.campaign_end
    assert a.url == "https://www.indiegogo.com/en/projects/gpdhk/gpd-win-max-3-handheld-gaming-laptop", a.url

    b = by["8162"]
    assert b.currency == "EUR", b.currency
    assert b.platform == "gamefound", b.platform
    assert b.goal == 35000.0, b.goal
    assert b.pct_funded == pp.pct_funded(b.price, 35000.0)

    c = by["11718"]
    assert c.currency == "USD", c.currency
    assert c.backers_count == 3409, (
        "the API publishes an exact count; the card renders it as '3.4k'")


@check("the site's own :443 and ?ref= are stripped from every url")
def test_url_normalisation():
    sp = pp.parse_search_api(SEARCH_API_PAYLOAD, page_index=0, sort="default")
    for r in sp.rows:
        assert ":443" not in r.url, r.url
        assert "ref=" not in r.url, r.url
    assert pp.normalise_url(
        "https://www.indiegogo.com:443/en/projects/a/b?ref=explore"
    ) == "https://www.indiegogo.com/en/projects/a/b"


@check("platform is decoded from the API flag and agrees with the url host")
def test_platform_agrees_with_host():
    sp = pp.parse_search_api(SEARCH_API_PAYLOAD, page_index=0, sort="default")
    for r in sp.rows:
        assert r.platform == pp.platform_from_url(r.url), (
            f"{r.sku}: platform={r.platform} but url host says "
            f"{pp.platform_from_url(r.url)}")


@check("the rendered card parses, and its counts are the LOSSY ones")
def test_card_dom_fallback():
    rows = pp.parse_search_html(CARD_HTML, page=1, sort="default")
    assert len(rows) == 1, f"expected one card, got {len(rows)}"
    r = rows[0]
    assert r.sku == "442684", r.sku
    assert r.price == 6556148.0, r.price
    assert r.currency == "HKD", r.currency
    assert r.price_source == "dom", r.price_source
    assert r.backers_count == 336, r.backers_count
    # "1.1k" -> 1100, which is NOT the API's 1065. That gap is the reason the
    # DOM is the fallback and not the primary source, and pinning it here is
    # what stops someone "fixing" the API path to match the card.
    assert r.followers_count == 1100, r.followers_count


@check("campaign page parse pins exact values, including the ISO currency")
def test_campaign_values():
    html = _campaign_html(CAMPAIGN_STATE)
    row = pp.parse_campaign(html, url="https://www.indiegogo.com/en/projects/gpdhk/x")
    assert row is not None
    assert row.sku == "442684", row.sku
    assert row.price == 6569871.0, row.price
    # The campaign page STATES the code; the search API only has a symbol.
    assert row.currency == "HKD", row.currency
    assert row.currency_iso_source == "checkout_currency", row.currency_iso_source
    assert row.backers_count == 337, row.backers_count
    assert row.followers_count == 1065, row.followers_count
    assert row.campaign_day == 16, row.campaign_day
    assert row.price_source == "detail", row.price_source
    assert row.platform == "indiegogo"
    # catalogCategory arrives as a bare int on this page and as a dict on the
    # search API; both must resolve to the same string.
    assert row.category == "Productivity", row.category


def _campaign_html(state):
    return ("<html><head></head><body><script>window.__INITIAL_STATE__ = "
            + json.dumps(state) + ";</script></body></html>")


@check("reward boxes parse, and the 30-day low is NOT read as a was-price")
def test_reward_values():
    rows = pp.parse_rewards(REWARD_HTML,
                            url="https://www.indiegogo.com/en/projects/gpdhk/x")
    assert len(rows) == 2, f"expected two reward boxes, got {len(rows)}"
    r = rows[0]
    assert r.sku == "3565509", r.sku
    assert r.title == "GPD WIN Max 3 388+32GB+1TB", r.title
    assert r.price == 13723.0, r.price
    assert r.original_price == 15677.0, r.original_price
    assert r.lowest_price_30d == 13723.0, r.lowest_price_30d
    assert r.discount_pct == 12.46, r.discount_pct
    assert r.remaining_stock == 79, r.remaining_stock
    assert r.purchased_count == 121, r.purchased_count
    assert r.is_featured is True
    assert r.estimated_delivery == "2026-12-15T00:00:00Z", r.estimated_delivery


@check("no reward reports a zero or negative discount")
def test_no_negative_discount():
    # The EU Omnibus 30-day-low sits in the same box as the struck-through
    # price and is nearly identical in markup. Read as a was-price it gives
    # `original_price` == `price` and a 0% discount on a reward that really
    # is discounted -- measured on this fixture, where the 30-day low equals
    # the current price on BOTH boxes while the real discount is 12-26%.
    for r in pp.parse_rewards(REWARD_HTML, url="https://x/en/projects/a/b"):
        if r.discount_pct is not None:
            assert r.discount_pct > 0, (
                f"reward {r.sku}: discount_pct={r.discount_pct}; the 30-day "
                f"low has probably been read as original_price")
        if r.original_price is not None and r.price is not None:
            assert r.original_price > r.price, (
                f"reward {r.sku}: original_price {r.original_price} is not "
                f"above price {r.price}")


# ---------------------------------------------------------------------------
# Money, counts and sentinels
# ---------------------------------------------------------------------------

@check("prefixed currency symbols are matched longest-first")
def test_currency_longest_first():
    assert pp.currency_from_symbol("HK$") == "HKD"
    assert pp.currency_from_symbol("HK$6,556,148") == "HKD", (
        "a bare $ must not swallow the HK prefix")
    assert pp.currency_from_symbol("A$") == "AUD"
    assert pp.currency_from_symbol("NZ$") == "NZD"
    assert pp.currency_from_symbol("C$") == "CAD"
    assert pp.currency_from_symbol("S$") == "SGD"
    assert pp.currency_from_symbol("$") == "USD"


@check("an ambiguous symbol yields a null currency, never a coin flip")
def test_currency_ambiguous():
    # The site itself uses "kr" for both its Norwegian krone (id 10) and its
    # Swedish krona (id 13). Its own table cannot tell them apart from the
    # symbol, so neither can this parser.
    assert "kr" in pp.AMBIGUOUS_CURRENCY_SYMBOLS
    assert pp.currency_from_symbol("kr") is None
    assert pp.currency_from_symbol("kr.") == "DKK", (
        "Danish krone IS distinguishable and must not be lumped in")
    assert pp.currency_from_symbol("") is None
    assert pp.currency_from_symbol(None) is None
    assert pp.currency_from_symbol("¤") is None, "unknown means null"


@check("no currency defaults to USD when the site said nothing")
def test_no_defaulted_currency():
    src = inspect.getsource(pp)
    assert 'currency = "USD"' not in src
    assert "currency or 'USD'" not in src
    assert 'currency", "USD"' not in src


@check("money parses the three grouping conventions and NBSP variants")
def test_parse_money():
    assert pp.parse_money("HK$6,556,148") == 6556148.0
    assert pp.parse_money("€12,576") == 12576.0
    assert pp.parse_money("1.234,56") == 1234.56
    assert pp.parse_money("1,234.56") == 1234.56
    assert pp.parse_money("$1,234") == 1234.0, "3 trailing digits = grouping"
    for space in (" ", " ", " ", " "):
        assert pp.parse_money(f"1{space}234,56") == 1234.56, repr(space)
    assert pp.parse_money("") is None
    assert pp.parse_money(None) is None
    assert pp.parse_money("no digits here") is None


@check("a percentage badge cannot lend its number the next price's symbol")
def test_percent_stripped_before_match():
    # Both word orders: German writes -16%, Turkish writes -%10,34.
    assert pp.parse_money("-16% €158,357") == 158357.0
    assert pp.parse_money("-%10,34 ₺25.999") == 25999.0


@check("abbreviated counts parse to the right order of magnitude")
def test_parse_count():
    # int(re.sub(r"\D","", "3.4k")) is 34 -- wrong by two orders of
    # magnitude, and silently so.
    assert pp.parse_count("3.4k") == 3400
    assert pp.parse_count("11.6k") == 11600
    assert pp.parse_count("1.1k") == 1100
    assert pp.parse_count("336") == 336
    assert pp.parse_count("2.1m") == 2_100_000
    assert pp.parse_count("") is None
    assert pp.parse_count(None) is None


@check("the API's not-applicable 0 never reaches a row")
def test_zero_sentinel_normalised():
    # `fundedInSeconds` is never null: a campaign that has not reached a goal
    # and one with no goal at all both report 0. Written through it reads as
    # "funded in zero seconds".
    assert pp._nz(0) is None
    assert pp._nz(None) is None
    assert pp._nz(127384) == 127384
    sp = pp.parse_search_api(SEARCH_API_PAYLOAD, page_index=0, sort="default")
    for r in sp.rows:
        assert r.funded_in_seconds != 0, (
            f"{r.sku} carries the 0 sentinel in funded_in_seconds")


@check("pct_funded is None rather than 0 when there is no goal")
def test_pct_funded_guards():
    assert pp.pct_funded(100.0, None) is None
    assert pp.pct_funded(100.0, 0) is None
    assert pp.pct_funded(None, 100.0) is None
    assert pp.pct_funded(12576.05, 12000.0) == 104.8


@check("days_left is computed from the absolute end, not the localised card")
def test_days_left():
    ref = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    assert pp.days_left("2026-10-17T02:00:00Z", now=ref) == 29
    assert pp.days_left("2026-09-01T00:00:00Z", now=ref) is None, "past = None"
    assert pp.days_left(None) is None
    assert pp.days_left("not a date") is None


@check("outcome_code is carried raw, with no invented success/fail label")
def test_outcome_not_labelled():
    # Measured: 20 of 60 sampled rows with outcome=0 had already PASSED their
    # goal. Reading it as "did this succeed" is wrong, and the site publishes
    # no names for the values.
    names = {f.name for f in fields(ow.Campaign)}
    assert "outcome_code" in names
    assert "outcome" not in names, (
        "a bare `outcome` invites a label this repo cannot justify")
    src = inspect.getsource(pp)
    for invented in ('"successful"', '"failed"', '"funded"', '"unsuccessful"'):
        assert invented not in src, f"{invented} is a guess, not a measurement"


# ---------------------------------------------------------------------------
# Pagination — the trap this site sets
# ---------------------------------------------------------------------------

@check("page_url() REFUSES: this listing has no per-page URL")
def test_page_url_refuses():
    # ?page=2 on /projects/search is accepted, answers 200, and serves the
    # IDENTICAL first 24 cards -- measured for `page`, `pageNumber` and `p`.
    # A ported page_url() would refetch page 1 forever, find no new ids,
    # conclude the listing was exhausted and report a COMPLETE run holding
    # 24 rows. So it raises instead of returning anything.
    try:
        pp.page_url("https://www.indiegogo.com/en/projects/search", 2)
    except pp.ListingNotAddressable:
        pass
    else:
        raise AssertionError("page_url() must refuse, not return a URL")
    assert page_flow.listing_is_url_addressable() is False


@check("pageIndex is 0-based, and page 1 of a run is pageIndex 0")
def test_page_index_is_zero_based():
    # The site's own arithmetic: pageIndex=0 returns firstItemNumber 1.
    assert SEARCH_API_PAYLOAD["projects"]["firstItemNumber"] == 1
    sp = pp.parse_search_api(SEARCH_API_PAYLOAD, page_index=0, sort="default")
    assert sp.page == 1, "page_index 0 is page 1 as printed"
    assert all(r.page == 1 for r in sp.rows)
    sp2 = pp.parse_search_api(SEARCH_API_PAYLOAD, page_index=1, sort="default")
    assert sp2.page == 2
    assert all(r.page == 2 for r in sp2.rows)
    body = pp.build_search_body("https://x/en/projects/search", 0)
    assert body["pageIndex"] == 0
    try:
        pp.build_search_body("https://x/en/projects/search", -1)
    except ValueError:
        pass
    else:
        raise AssertionError("a negative pageIndex must be refused")


@check("the site's own page count is trusted, minus its measured off-by-one")
def test_plan_page_indices():
    # totalPageCount is 417 for the default query, and pageIndex=416 returns
    # ZERO items while 415 returns a full 24 -- binary-searched live. So the
    # last index worth requesting is total_pages - 2.
    assert page_flow.plan_page_indices(3, 417) == [0, 1, 2]
    assert page_flow.plan_page_indices(500, 417)[-1] == 415
    assert 416 not in page_flow.plan_page_indices(500, 417)
    assert page_flow.plan_page_indices(5, 3) == [0, 1]
    assert page_flow.plan_page_indices(0, 417) == []
    assert page_flow.reachable_max(417) == 9984
    assert page_flow.reachable_max(None) is None


@check("a capped query is still `complete`, and says so in the sidecar")
def test_capped_is_complete():
    assert "site_result_cap" in ow.COMPLETE_STOP_REASONS, (
        "walking to the end of what the site serves IS complete")
    meta = ow.run_meta("complete", "site_result_cap", 500, 416,
                       "u", "u", 9984, total_results=10000,
                       pages_available=417, capped_by_site=True,
                       reachable_max=9984)
    # A sidecar saying only "complete" would be lying by omission.
    for key in ("total_results", "pages_available", "capped_by_site",
                "reachable_max", "sort"):
        assert key in meta, key
    assert meta["capped_by_site"] is True
    assert meta["reachable_max"] == 9984


@check("filters from a search URL reach the request body")
def test_build_search_body_filters():
    body = pp.build_search_body(
        "https://www.indiegogo.com/en/projects/search"
        "?projectCatalogCategories=BoardAndCardGames&term=solar", 2,
        sort="most-funded")
    assert body["pageIndex"] == 2
    assert body["term"] == "solar"
    assert body["projectCatalogCategories"] == ["BoardAndCardGames"], body
    assert body["sortType"] == pp.SORT_TYPES["most-funded"] == 4
    # The API accepts the string form and the numeric enum identically
    # (measured: same total, same first id), so no enum table is maintained.


@check("the site's own params object seeds the body, overriding our guess")
def test_body_from_site_params():
    params = {"sortType": 4, "term": None, "pageIndex": None, "pageSize": 24,
              "projectCatalogCategories": [11], "creatorName": None}
    body = pp.body_from_site_params(params, 3)
    assert body["pageIndex"] == 3
    assert body["sortType"] == 4
    assert body["projectCatalogCategories"] == [11]
    # The site writes null where the body wants an empty list; the template's
    # shape must survive.
    assert body["term"] == "", body["term"]
    assert body["projectTags"] == []
    assert body["creator"] == {"creatorID": None, "name": None}


@check("sorts are the site's own, and --sort overrides a URL's")
def test_sorts():
    assert pp.SORT_TYPES["default"] == 0
    assert pp.SORT_TYPES["most-funded"] == 4
    assert pp.SORT_TYPES["ending-soon"] == 7
    assert len(pp.SORT_TYPES) == 10, "the site's menu has ten entries"
    assert pp.sort_from_url("https://x/en/projects/search") == "default"
    assert pp.sort_from_url("https://x/en/projects/search?sortType=4") == "most-funded"
    assert pp.sort_from_url("https://x/en/projects/search?sortType=4",
                            "newest") == "newest", "an explicit --sort wins"
    # The legacy ?sort=trending parameter is NOT honoured by the current site.
    assert pp.sort_from_url("https://x/explore/all?sort=trending") == "default"


@check("sort rides on every row, because it decides WHICH rows exist")
def test_sort_is_a_column():
    names = {f.name for f in fields(ow.Campaign)}
    assert "sort" in names
    sp = pp.parse_search_api(SEARCH_API_PAYLOAD, page_index=0, sort="most-funded")
    assert all(r.sort == "most-funded" for r in sp.rows)


@check("position is unique across a merged multi-page run")
def test_page_and_position_unique():
    # `position` restarting at 1 on each page would have 24 rows claiming a
    # position another row already held; the column is worthless without it.
    rows = []
    for idx in (0, 1, 2):
        rows.extend(pp.parse_search_api(SEARCH_API_PAYLOAD, page_index=idx,
                                        sort="default").rows)
    for i, r in enumerate(rows, start=1):
        r.position = i
    pairs = {(r.page, r.position) for r in rows}
    assert len(pairs) == len(rows), "page+position is not unique"
    assert len({r.position for r in rows}) == len(rows)


# ---------------------------------------------------------------------------
# Page state — markers, ordering, policy
# ---------------------------------------------------------------------------

@check("cf-turnstile is NOT a marker, and challenges.cloudflare.com is")
def test_marker_set():
    # Counted 2026-09-17 across nine captures:
    #   cf-turnstile               1 on all six served pages, 1 on the 404,
    #                              1 on a browser-fetched challenge,
    #                              0 on a curl-fetched challenge
    #   challenges.cloudflare.com  0 on all seven served-or-404 captures,
    #                              5-6 on both challenges
    # It fires on every page fetched through the Scraping Browser, whose
    # auto-solve extension injects a turnstile hunter into everything it
    # loads, and is ABSENT from the one challenge fetched without it.
    joined = " ".join(pp.BOT_CHALLENGE_MARKERS).lower()
    assert "cf-turnstile" not in joined, (
        "cf-turnstile fires on good pages and misses a real challenge")
    assert "challenges.cloudflare.com" in pp.BOT_CHALLENGE_MARKERS
    assert CHALLENGE_HTML.count("challenges.cloudflare.com") > 0
    assert CARD_HTML.count("challenges.cloudflare.com") == 0


@check("every marker is absent from a page the site really served")
def test_markers_absent_from_good_page():
    # The rule that promoted itself out of a comment: before adding ANY
    # marker, count it on a page you know is good.
    served = _served_search_html()
    for marker in pp.BOT_CHALLENGE_MARKERS:
        assert marker.lower() not in served.lower(), (
            f"{marker!r} appears on a SERVED page -- it is a fact about the "
            f"site, not a marker")


def _served_search_html():
    payload = json.dumps({"props": {"params": {"sortType": 0},
                                    "result": SEARCH_API_PAYLOAD}})
    return ("<html><head><link href='https://cdn.static.indiegogo.com/a.css'>"
            "<link href='https://cdn.static.indiegogo.com/b.css'></head><body>"
            "<script>App.registerComponent('v-1', "
            "'App.Components.Search.SearchProjectsResults', Ctor, "
            + payload + ");</script>" + CARD_HTML + "</body></html>")


@check("page states classify correctly on real captures")
def test_detect_page_state():
    served = _served_search_html()
    assert pp.detect_page_state(served, status=200) == "content"
    assert pp.detect_page_state(CHALLENGE_HTML, status=403) == "captcha"
    assert pp.detect_page_state(NOT_FOUND_HTML, status=404,
                                mode="campaign") == "not_found"
    empty = ('<html><body><span data-qa="search-count:0">(0 results)</span>'
             '<link href="https://cdn.static.indiegogo.com/a.css">'
             '<link href="https://cdn.static.indiegogo.com/b.css"></body></html>')
    assert pp.detect_page_state(empty, status=200) == "empty"


@check("an unambiguous no-results signal outranks the asset heuristic")
def test_classification_order():
    # The ordering trap: a threshold-based heuristic running ahead of a
    # positive marker reported exit 3 for a correct answer in a sibling repo.
    # A minimal real page with FEWER asset references than the measured 37-55
    # must still classify as `empty`, not `blocked`.
    minimal_empty = ('<html><body><span data-qa="search-count:0">0</span>'
                     '</body></html>')
    assert pp.detect_page_state(minimal_empty, status=200) == "empty"
    # And the site's own 404 must not be called a block even though it, too,
    # carries zero asset references.
    assert pp.detect_page_state(NOT_FOUND_HTML, status=404) == "not_found"


@check("a served page with no vendor marker is still recognised as served")
def test_structural_asset_signal():
    # Chromium's own network-error page carries the site's hostname in its
    # <title> and no vendor marker of any kind. Only "was this built out of
    # the site's own assets?" answers correctly.
    chrome_error = ("<html><head><title>www.indiegogo.com</title></head><body>"
                    "<div>ERR_PROXY_CONNECTION_FAILED</div></body></html>")
    assert pp.detect_page_state(chrome_error, status=None) == "blocked"
    assert pp.SITE_ASSET_HOST == "cdn.static.indiegogo.com"
    assert pp.MIN_ASSET_REFERENCES >= 2


@check("the cf-mitigated response header alone is enough to call a challenge")
def test_cf_mitigated_header():
    marker = pp.challenge_marker("<html></html>",
                                 {"cf-mitigated": "challenge"})
    assert marker == "cf-mitigated: challenge", marker
    assert pp.challenge_marker("<html></html>", {"cf-mitigated": "nope"}) is None
    assert pp.challenge_marker("<html></html>", None) is None


@check("markers are matched against an entity-unescaped, BOUNDED prefix")
def test_marker_normalisation():
    escaped = "challenges&#46;cloudflare&#46;com"
    assert pp.challenge_marker(escaped) == "challenges.cloudflare.com", (
        "an edge may entity-escape the punctuation in its own marker")
    # Bounded, so a campaign title deep in a 630 KB grid cannot read as one.
    assert pp.MARKER_SCAN_BYTES <= 128 * 1024
    far_away = ("x" * (pp.MARKER_SCAN_BYTES + 10)) + "challenges.cloudflare.com"
    assert pp.challenge_marker(far_away) is None


@check("STATE_POLICY gives each state the response it deserves")
def test_state_policy():
    # A correct answer must never be retried or paid for.
    assert not page_flow.should_retry("empty")
    assert not page_flow.counts_as_blocked("empty")
    assert not page_flow.should_retry("not_found")
    assert not page_flow.counts_as_blocked("not_found")
    # A challenge is worth a solve; a widget-less refusal is not.
    assert page_flow.should_solve("captcha")
    assert page_flow.counts_as_blocked("captcha")
    assert not page_flow.should_solve("blocked"), (
        "'unsolvable' is a property of a page; a page with no widget is one")
    assert page_flow.counts_as_blocked("blocked")
    # Served-but-not-painted waits rather than refetching a good page.
    assert page_flow.should_wait("unpainted")
    assert not page_flow.should_retry("unpainted")
    assert not page_flow.counts_as_blocked("unpainted")
    assert not page_flow.should_retry("content")


@check("every state the classifier can emit has a policy entry")
def test_every_state_has_a_policy():
    src = inspect.getsource(pp.detect_page_state)
    emitted = set(re.findall(r'return "([a-z_]+)"', src))
    assert emitted, "could not find the classifier's return values"
    missing = emitted - set(page_flow.STATE_POLICY)
    assert not missing, f"states with no policy: {sorted(missing)}"


# ---------------------------------------------------------------------------
# The engines: import shape, flag parity, signatures, dead names
# ---------------------------------------------------------------------------

ENGINES = ("playwright_scraper", "puppeteer_scraper", "selenium_scraper")
ENGINE_DRIVER_IMPORT = {
    "playwright_scraper": "playwright",
    "puppeteer_scraper": "pyppeteer",
    "selenium_scraper": "selenium",
}


def _engine(name):
    """Import an engine, or record a skip. Never a silent pass."""
    try:
        return __import__(name)
    except ImportError as e:
        SKIPPED.append(f"{name} ({e.name or e})")
        return None


@check("each engine imports its driver at MODULE level")
def test_driver_import_is_module_level():
    # An engine that imports its driver inside the launch path imports
    # cleanly with no driver installed, so the "skipped, engine absent" group
    # never skips and the CI job that exists to fail on unexpected skips
    # cannot catch a broken import. It also lets CI run against a stub
    # version with nothing noticing. This drifts back silently, so it is
    # asserted from the SOURCE rather than from the imported module.
    for name, driver in ENGINE_DRIVER_IMPORT.items():
        src = open(os.path.join(HERE, f"{name}.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        top_level = set()
        for node in tree.body:                     # module body ONLY
            if isinstance(node, ast.Import):
                top_level.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top_level.add(node.module.split(".")[0])
        assert driver in top_level, (
            f"{name} does not import {driver} at module level, so it will "
            f"import cleanly with the driver absent")


@check("the three engines expose flag-for-flag identical CLIs")
def test_flag_parity():
    # Assert in BOTH directions: a new unshared flag fails, and so does
    # closing a difference. When this check was first written in a sibling
    # repo it found TWELVE flags the primary engine had and its twins did
    # not, nine of them predating the work, while the README promised "same
    # CLI".
    sets = {}
    for name in ENGINES:
        src = open(os.path.join(HERE, f"{name}.py"), encoding="utf-8").read()
        block = src[src.index("def parse_args"):]
        sets[name] = set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', block))
    ref = sets["playwright_scraper"]
    assert len(ref) >= 30, f"only {len(ref)} flags found; the parse is wrong"
    for name, flags in sets.items():
        assert flags == ref, (
            f"{name} differs: missing={sorted(ref - flags)} "
            f"extra={sorted(flags - ref)}")


@check("the family's flag contract is present in every engine")
def test_family_flag_contract():
    # Re-derived across the sibling repos rather than taken from a list:
    # these 22 appear in all of them, and the next five in all but one.
    contract = {
        "--url", "--pages", "--category", "--format", "--out", "--delay",
        "--retries", "--retry-delay", "--concurrency", "--proxy",
        "--proxy-file", "--proxy-rotate", "--proxy-shuffle",
        "--proxy-block-retries", "--twocaptcha-key", "--captcha-api",
        "--solve-captcha", "--min-score", "--cdp-endpoint", "--allow-empty",
        "--dump-html", "--headless", "--headful",
        "--fingerprint", "--fp-country", "--fp-tags", "--locale", "--mode",
    }
    for name in ENGINES:
        src = open(os.path.join(HERE, f"{name}.py"), encoding="utf-8").read()
        block = src[src.index("def parse_args"):]
        flags = set(re.findall(r'add_argument\(\s*"(--[a-z0-9-]+)"', block))
        missing = contract - flags
        assert not missing, f"{name} is missing {sorted(missing)}"


@check("removed and banned flags stay removed")
def test_banned_flags():
    # Scoped to the ENGINES: --country is banned on a scraper (it could
    # disagree with the URL) and legitimate on fingerprint_client.py, where
    # it picks a fingerprint locale.
    banned = ("--antidetect", "--country", "--proxy-server")
    for name in ENGINES:
        src = open(os.path.join(HERE, f"{name}.py"), encoding="utf-8").read()
        block = src[src.index("def parse_args"):]
        for flag in banned:
            assert f'"{flag}"' not in block, f"{name} reintroduced {flag}"


@check("every shared-module call binds against the callee's real signature")
def test_shared_call_signatures():
    # This is the check that catches what import, --help, compileall and an
    # undefined-name walk all miss, because none of those calls a function
    # the way a live run does. Written for this repo, it immediately found
    # `solve_recaptcha(..., proxy=...)` -- a kwarg this repo's solver does
    # not take, which would have crashed on the first solve attempt.
    import importlib
    shared = {}
    for m in ("product_parser", "page_flow", "output_writer", "proxy_pool",
              "captcha_solver", "env_config"):
        shared[m] = importlib.import_module(m)

    problems = []
    for name in ENGINES:
        src = open(os.path.join(HERE, f"{name}.py"), encoding="utf-8").read()
        tree = ast.parse(src)
        alias = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in shared:
                for a in node.names:
                    alias[a.asname or a.name] = (node.module, a.name)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in shared:
                        alias[a.asname or a.name] = (a.name, None)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn, target, label = node.func, None, None
            if isinstance(fn, ast.Name) and fn.id in alias and alias[fn.id][1]:
                mod, attr = alias[fn.id]
                target, label = getattr(shared[mod], attr, None), f"{mod}.{attr}"
            elif (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name)
                  and fn.value.id in alias and alias[fn.value.id][1] is None):
                mod = alias[fn.value.id][0]
                target = getattr(shared[mod], fn.attr, None)
                label = f"{mod}.{fn.attr}"
            if target is None or not callable(target):
                continue
            if (any(isinstance(a, ast.Starred) for a in node.args)
                    or any(k.arg is None for k in node.keywords)):
                continue
            try:
                sig = inspect.signature(target)
            except (TypeError, ValueError):
                continue
            kw = {k.arg for k in node.keywords if k.arg}
            try:
                sig.bind(*[object()] * len(node.args),
                         **{k: object() for k in kw})
            except TypeError as e:
                problems.append(f"{name}:{node.lineno} {label} -> {e}")
    assert not problems, "signature mismatches:\n  " + "\n  ".join(problems)


@check("no module uses a name it never imports, defines or assigns")
def test_no_undefined_names():
    # Covers exactly the branches an offline suite cannot execute -- a
    # NameError on a line reached only while fetching survives import,
    # --help, compileall and a green suite. Kept COARSE (pooled bindings, no
    # scope tracking) so it under-reports rather than inventing problems.
    import builtins
    # scraper_api_client.py is in this list because it was NOT, and a
    # NameError at its finish_run call survived import, --help, compileall
    # and a green suite -- it fired only at the very end of an otherwise
    # successful live run, after the fetch and the parse had both worked.
    modules = list(ENGINES) + ["product_parser", "page_flow", "output_writer",
                               "scraper_api_client", "diff_runs",
                               "captcha_solver", "fingerprint_client",
                               "proxy_pool", "env_config"]
    for name in modules:
        path = os.path.join(HERE, f"{name}.py")
        tree = ast.parse(open(path, encoding="utf-8").read())
        bound = set(dir(builtins)) | {"__name__", "__doc__", "__file__"}
        for n in ast.walk(tree):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                for a in n.names:
                    bound.add((a.asname or a.name).split(".")[0])
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bound.add(n.name)
                args = n.args
                for arg in (list(args.args) + list(args.kwonlyargs)
                            + list(args.posonlyargs)):
                    bound.add(arg.arg)
                if args.vararg:
                    bound.add(args.vararg.arg)
                if args.kwarg:
                    bound.add(args.kwarg.arg)
            elif isinstance(n, ast.Lambda):
                a = n.args
                for arg in list(a.args) + list(a.kwonlyargs) + list(a.posonlyargs):
                    bound.add(arg.arg)
                if a.vararg:
                    bound.add(a.vararg.arg)
                if a.kwarg:
                    bound.add(a.kwarg.arg)
            elif isinstance(n, ast.ClassDef):
                bound.add(n.name)
            elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
                bound.add(n.id)
            elif isinstance(n, ast.ExceptHandler) and n.name:
                bound.add(n.name)
            elif isinstance(n, ast.Global):
                bound.update(n.names)
        used = {n.id for n in ast.walk(tree)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        missing = sorted(used - bound)
        assert not missing, f"{name}.py uses undefined name(s): {missing}"


@check("no policy constant is defined and never read")
def test_no_unread_policy_constants():
    # A constant carrying a paragraph of justification that nothing consults
    # is the same defect as dead code, and harder to see because the prose
    # reads like enforcement.
    src = {name: open(os.path.join(HERE, f"{name}.py"), encoding="utf-8").read()
           for name in list(ENGINES) + ["product_parser", "page_flow",
                                        "output_writer"]}
    policy_module = open(os.path.join(HERE, "page_flow.py"), encoding="utf-8").read()
    constants = re.findall(r"^([A-Z][A-Z0-9_]{3,})\s*=", policy_module, re.M)
    assert constants, "no constants found in page_flow.py"
    for const in constants:
        readers = [m for m, s in src.items()
                   if m != "page_flow" and const in s]
        accessor = re.search(rf"\b{const}\b", policy_module[policy_module.index(const) + len(const):])
        assert readers or accessor, (
            f"page_flow.{const} has no consumer anywhere -- either wire it up "
            f"or delete it")


# ---------------------------------------------------------------------------
# Output, exit codes and the sidecar
# ---------------------------------------------------------------------------

@check("a run that finds nothing writes nothing")
def test_empty_run_writes_nothing():
    with tempfile.TemporaryDirectory() as d:
        prefix = os.path.join(d, "out")
        rc = ow.save([], prefix, "both")
        assert rc == ow.EXIT_NO_PRODUCTS
        assert not os.path.exists(prefix + ".json"), (
            "an empty result must not replace last night's good output")
        assert not os.path.exists(prefix + ".csv")
        # --allow-empty is the opt-out.
        rc = ow.save([], prefix, "both", allow_empty=True,
                     row_cls=ow.Campaign)
        assert os.path.exists(prefix + ".json")


@check("an empty CSV still carries its header")
def test_empty_csv_has_header():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "e.csv")
        ow.write_csv([], path, row_cls=ow.Campaign)
        with open(path, encoding="utf-8") as f:
            header = next(csv.reader(f))
        assert header == [f.name for f in fields(ow.Campaign)]
        # ...and the rewards schema gets the rewards columns, not Campaign's.
        path2 = os.path.join(d, "r.csv")
        ow.write_csv([], path2, row_cls=ow.Reward)
        with open(path2, encoding="utf-8") as f:
            header2 = next(csv.reader(f))
        assert header2 == [f.name for f in fields(ow.Reward)]


@check("a failed run writes NO sidecar beside good data")
def test_failed_run_writes_no_sidecar():
    with tempfile.TemporaryDirectory() as d:
        prefix = os.path.join(d, "out")
        rc = ow.finish_run([], prefix, "json", False, blocked=True,
                           stop_reason="blocked", pages_requested=3,
                           pages_completed=0, start_url="u", final_url="u",
                           mode="search")
        assert rc == ow.EXIT_BLOCKED
        assert not os.path.exists(prefix + ".meta.json"), (
            "a 'failed' sidecar beside yesterday's good data contradicts it")


@check("blocked, empty and partial get three distinct exit codes")
def test_exit_codes():
    assert (ow.EXIT_BLOCKED, ow.EXIT_NO_PRODUCTS, ow.EXIT_PARTIAL,
            ow.EXIT_REMOTE_API_ERROR) == (3, 4, 6, 5)
    with tempfile.TemporaryDirectory() as d:
        rows = pp.parse_search_api(SEARCH_API_PAYLOAD, page_index=0,
                                   sort="default").rows
        # partial: rows, but stopped early
        rc = ow.finish_run(rows, os.path.join(d, "p"), "json", False,
                           blocked=False, stop_reason="page_failed",
                           pages_requested=5, pages_completed=2,
                           start_url="u", final_url="u", mode="search")
        assert rc == ow.EXIT_PARTIAL
        meta = json.load(open(os.path.join(d, "p.meta.json")))
        assert meta["status"] == "partial"
        # complete
        rc = ow.finish_run(rows, os.path.join(d, "c"), "json", False,
                           blocked=False, stop_reason="completed",
                           pages_requested=1, pages_completed=1,
                           start_url="u", final_url="u", mode="search")
        assert rc == 0
        assert json.load(open(os.path.join(d, "c.meta.json")))["status"] == "complete"


@check("the sidecar records WHICH pages failed, by number")
def test_sidecar_names_failed_pages():
    # A count stops being a description once a page can fail while later ones
    # succeed.
    meta = ow.run_meta("partial", "page_failed", 5, 3, "u", "u", 72,
                       pages_failed=[3, 5], mode="search", sort="default")
    assert meta["pages_failed"] == [3, 5]
    assert meta["mode"] == "search"
    assert meta["sort"] == "default"


@check("dedupe keeps a keyless row rather than losing it silently")
def test_dedupe():
    a = ow.Campaign(sku="1")
    b = ow.Campaign(sku="1")
    c = ow.Campaign(sku=None)
    d = ow.Campaign(sku=None)
    seen = set()
    out = ow.dedupe_by_key([a, b, c, d], seen, key="sku")
    assert len(out) == 3, "both keyless rows must survive; only the dup goes"


@check("a list column round-trips through CSV")
def test_list_column_csv():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.csv")
        row = ow.Campaign(sku="1", tags=["Fantasy", "Strategy"])
        ow.write_csv([row], path, row_cls=ow.Campaign)
        with open(path, encoding="utf-8") as f:
            data = list(csv.DictReader(f))
        assert data[0]["tags"] == "Fantasy | Strategy"
        assert data[0]["tags"].split(ow.LIST_CSV_SEPARATOR) == ["Fantasy", "Strategy"]


# ---------------------------------------------------------------------------
# Configuration and secrets
# ---------------------------------------------------------------------------

@check(".env.example documents exactly the variables the code reads")
def test_env_example_matches_env_keys():
    path = os.path.join(HERE, ".env.example")
    assert os.path.exists(path), ".env.example is missing"
    documented = set()
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        documented.add(line.split("=", 1)[0].strip())
    known = set(env_config.ENV_KEYS)
    assert documented == known, (
        f"documented-but-unread: {sorted(documented - known)}; "
        f"read-but-undocumented: {sorted(known - documented)}")


@check("a copied .env.example reads as UNSET, not as configured")
def test_copied_env_example_is_unset():
    # The credentialled URLs are documented the way the vendor documents
    # them, with {braces}. A literal placeholder list does not match those,
    # so `cp .env.example .env` followed by a run would connect with the
    # string "{login}-zone-..." as its username and get a 401 -- a confusing
    # auth error a long way from its cause.
    path = os.path.join(HERE, ".env.example")
    saved = dict(os.environ)
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()
        for key in env_config.ENV_KEYS:
            assert env_config.env_value(key) is None, (
                f"{key}'s example value reads as configured; a braced "
                f"placeholder must count as unset")
    finally:
        os.environ.clear()
        os.environ.update(saved)


@check("no variable is mapped onto a flag that already has a default")
def test_no_inert_env_mapping():
    # `apply()` only fills a FALSY destination, so a variable pointed at a
    # flag with a non-empty argparse default is silently inert -- a setting
    # that looks configurable and is not.
    for name in ENGINES:
        src = open(os.path.join(HERE, f"{name}.py"), encoding="utf-8").read()
        block = src[src.index("def parse_args"):]
        for env_name, dest in env_config.ENV_KEYS.items():
            flag = "--" + dest.replace("_", "-")
            m = re.search(rf'add_argument\(\s*"{re.escape(flag)}"([^)]*)\)',
                          block, re.S)
            if not m:
                continue
            default = re.search(r"default\s*=\s*([^,)]+)", m.group(1))
            if default and default.group(1).strip() not in ("None",):
                raise AssertionError(
                    f"{name}: {env_name} -> {flag}, but {flag} has a "
                    f"non-empty default ({default.group(1).strip()}), so the "
                    f"variable can never be read")


@check("credentials are masked GLOBALLY, and the host and port are kept")
def test_credential_masking():
    eng = _engine("playwright_scraper")
    if eng is None:
        return
    # `user:secret@` rather than an invented pair: .github/ci_checks.py
    # keeps an explicit allowlist of documented placeholder shapes, and a
    # fixture outside it turns the build red for no reason -- which is the
    # exact way a shipped check in a sibling repo came to fail on its own
    # main branch.
    text = ("ws://user:secret@cb.2captcha.com:9222 failed; retried "
            "ws://user:secret@cb.2captcha.com:9222 and "
            "ws://user:secret@cb.2captcha.com:9222")
    masked = eng._mask_credentials(text)
    assert "user:" not in masked and "secret" not in masked, masked
    # A masker that handles the first occurrence prints the password the
    # other two times and looks like it is working.
    assert masked.count("***:***") == 3, masked
    # Which exit a run used is the point of the log and is not the secret.
    assert "cb.2captcha.com:9222" in masked
    # A key in a query string leaks through almost every library exception.
    q = eng._mask_credentials("https://api.2captcha.com/x?key=abc123&b=1")
    assert "abc123" not in q, q
    assert "key=***" in q, q


@check("no credential-shaped string is committed anywhere in the repo")
def test_no_committed_credentials():
    patterns = [
        (re.compile(r"\b[0-9a-f]{32}\b"), "a 32-hex string reads as a live API key"),
        (re.compile(r"[a-z]+://[^\s/@\"']+:[^\s/@\"']+@"), "inline URL credentials"),
    ]
    allow = ("smoke_test.py", ".env.example", "README.md", "CHANGELOG.md",
             "TROUBLESHOOTING.md")
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs
                   if d not in (".git", "__pycache__", ".venv", "venv",
                                "node_modules", ".pytest_cache")]
        for fn in files:
            if fn in allow or fn.endswith((".png", ".jpg", ".ico")):
                continue
            if fn == ".env":
                continue          # local-only, gitignored, never committed
            path = os.path.join(root, fn)
            try:
                text = open(path, encoding="utf-8").read()
            except (UnicodeDecodeError, OSError):
                continue
            for rx, why in patterns:
                for m in rx.finditer(text):
                    frag = m.group(0)
                    if "***" in frag or "{" in frag or "user:pass" in frag:
                        continue
                    if "example.com" in frag or "SCRUBBED" in frag:
                        continue
                    # Documentation placeholders. Named explicitly rather
                    # than by a loose pattern: a shipped check in a sibling
                    # repo FAILED on its own main branch because its
                    # allowlist had drifted, and a check nobody can read is
                    # a check nobody runs.
                    if any(ph in frag for ph in ("login:password",
                                                 "user:pass",
                                                 "username:password",
                                                 "something:something",
                                                 "USER:PASS")):
                        continue
                    raise AssertionError(
                        f"{os.path.relpath(path, HERE)}: {why}: {frag[:40]}")


@check("committed fixtures carry no per-project access tokens")
def test_fixtures_are_scrubbed():
    # Guarded by SHAPE, not by the old literals, so the NEXT capture is
    # caught too. The site issues a `draftAccessKey` per project; anonymous,
    # but not ours to republish, and an opaque token in a public repo reads
    # as a live credential to every scanner that looks.
    for item in SEARCH_API_PAYLOAD["projects"]["pagedItems"]:
        key = item.get("draftAccessKey")
        if key is None:
            continue
        assert key == "SCRUBBED-NOT-VERBATIM", (
            f"project {item.get('projectID')} still carries a real "
            f"draftAccessKey in the committed fixture")
    src = open(os.path.join(HERE, "smoke_test.py"), encoding="utf-8").read()
    assert not re.search(r'"draftAccessKey":\s*"[A-Z0-9]{20,}"', src), (
        "a raw access token is present in the fixture block")


# ---------------------------------------------------------------------------
# Wording enforced by a test
# ---------------------------------------------------------------------------

BANNED_PHRASES = (
    "cloud browser",
    "antidetect browser",
    "2scraper antidetect browser",
    "gate.2prx.com",
    "antidetect_local_api",
    "--antidetect",
)


@check("no shipped file uses a banned product name")
def test_banned_wording():
    # These are four separately-billed 2Captcha products behind one key, and
    # the names matter. A sibling repo shipped the exact phrase this check
    # bans into two public repo DESCRIPTIONS, because the suite scans repo
    # FILES and a GitHub description is not a file -- see
    # test_repo_metadata_wording_is_documented below.
    for root, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs
                   if d not in (".git", "__pycache__", ".venv", "venv",
                                ".pytest_cache")]
        for fn in files:
            if not fn.endswith((".py", ".md", ".yml", ".yaml", ".txt", ".toml")):
                continue
            if fn == "smoke_test.py":
                continue          # this file names them in order to ban them
            path = os.path.join(root, fn)
            try:
                text = open(path, encoding="utf-8").read().lower()
            except (UnicodeDecodeError, OSError):
                continue
            for phrase in BANNED_PHRASES:
                assert phrase not in text, (
                    f"{os.path.relpath(path, HERE)} uses the banned phrase "
                    f"{phrase!r}; write 'Scraping Browser API' instead")


@check("the README never claims a captcha cannot be solved")
def test_no_unsolvable_claim():
    # The most expensive shape of error this family can produce: no test
    # fails, no run crashes, the output is correct, and it tells a reader not
    # to buy something that would have worked. The only sentence this repo is
    # entitled to is "this repo does not implement X".
    path = os.path.join(HERE, "README.md")
    if not os.path.exists(path):
        return
    text = open(path, encoding="utf-8").read().lower()
    for claim in ("cannot be solved", "impossible to solve",
                  "no solver can", "unsolvable by 2captcha",
                  "the solver is inapplicable", "a key would not help"):
        assert claim not in text, (
            f"README claims {claim!r}. 'Unsolvable' is a property of a PAGE "
            f"(one carrying no widget), never of a vendor.")


@check("the Turnstile task type this repo implements is named, not implied")
def test_turnstile_is_implemented():
    src = inspect.getsource(captcha_solver)
    assert "TurnstileTaskProxyless" in src, (
        "this site's only challenge is a Cloudflare Turnstile; the task type "
        "must be named in the solver rather than assumed")
    assert "chlPageData" in src, (
        "a Cloudflare Challenge page needs sitekey+action+cData+chlPageData")


@check("the turnstile.render interception is installed by every engine")
def test_turnstile_hook_installed_everywhere():
    # A Cloudflare Challenge page publishes no sitekey anywhere in its
    # markup: Cloudflare calls turnstile.render(container, params) once and
    # keeps nothing. Without the hook the challenge is not merely harder to
    # solve, it is impossible to REQUEST. Each driver spells it differently,
    # which is exactly why it cannot live in the shared module.
    spellings = {
        "playwright_scraper": "add_init_script",
        "puppeteer_scraper": "evaluateOnNewDocument",
        "selenium_scraper": "Page.addScriptToEvaluateOnNewDocument",
    }
    for name, spelling in spellings.items():
        src = open(os.path.join(HERE, f"{name}.py"), encoding="utf-8").read()
        assert "TURNSTILE_INTERCEPT_JS" in src, f"{name} never installs the hook"
        assert spelling in src, f"{name} should install it via {spelling}"


@check("the readiness wait never hands the browser a string to evaluate")
def test_no_evaluated_string_wait():
    # A site whose CSP lacks `unsafe-eval` refuses an evaluated string
    # outright; in a sibling repo that was an EvalError and exit 1 on the
    # site's most obvious URL. Polling querySelectorAll through the protocol
    # is a CDP call and works under any CSP.
    for name in ENGINES:
        src = _code_only(os.path.join(HERE, f"{name}.py"))
        for banned in ("wait_for_function", "waitForFunction"):
            assert banned not in src, (
                f"{name} uses {banned}, which evaluates a STRING")


@check("no JavaScript crosses the page_flow boundary")
def test_page_flow_has_no_js():
    # Selenium's execute_script takes a function BODY with an explicit
    # return, while Playwright and pyppeteer take `() => expr`. Naming the
    # OPERATION instead is what keeps this module from acquiring one
    # driver's dialect.
    src = _code_only(os.path.join(HERE, "page_flow.py"))
    for js in ("querySelectorAll", "=>", "document.", "return document"):
        assert js not in src, f"page_flow.py contains JavaScript: {js!r}"


# ---------------------------------------------------------------------------
# Docker, CI and the sample output
# ---------------------------------------------------------------------------

@check("the Dockerfile COPYs every module the entrypoint imports")
def test_dockerfile_copies_import_graph():
    # An explicit COPY list is right -- the image should carry no test suite
    # and no stray .env -- but it falls behind, and CI never builds the
    # image. Three repos in this family shipped an image that died with
    # ModuleNotFoundError on EVERY invocation, --help included, because one
    # module was missing from the list. This check needs no Docker.
    path = os.path.join(HERE, "Dockerfile")
    assert os.path.exists(path), "Dockerfile is missing"
    docker = open(path, encoding="utf-8").read()
    # Join shell line-continuations first: the COPY list spans several lines,
    # and a line-anchored regex silently matches none of them -- which is a
    # check that passes for the wrong reason, the exact failure mode this
    # whole check exists to prevent.
    docker = re.sub(r"\\\s*\n\s*", " ", docker)
    copied = set()
    for m in re.finditer(r"^COPY\s+(.+?)\s+\S+\s*$", docker, re.M):
        for token in m.group(1).split():
            copied.add(os.path.basename(token))
    assert copied, "no COPY sources parsed out of the Dockerfile"

    # Only the ENTRYPOINT's graph. The image deliberately ships one engine
    # (the one the README recommends), not all three.
    entry = re.search(r'ENTRYPOINT\s+\[([^\]]+)\]', docker)
    assert entry, "no ENTRYPOINT in the Dockerfile"
    entry_mod = None
    for token in re.findall(r'"([^"]+)"', entry.group(1)):
        if token.endswith(".py"):
            entry_mod = token[:-3]
    assert entry_mod, "could not tell which module the ENTRYPOINT runs"

    local = {f[:-3] for f in os.listdir(HERE) if f.endswith(".py")}
    needed, seen = set(), set()
    stack = [entry_mod]
    while stack:
        mod = stack.pop()
        if mod in seen:
            continue
        seen.add(mod)
        src_path = os.path.join(HERE, f"{mod}.py")
        if not os.path.exists(src_path):
            continue
        needed.add(f"{mod}.py")
        tree = ast.parse(open(src_path, encoding="utf-8").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in local:
                stack.append(node.module)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in local:
                        stack.append(a.name)
    missing = sorted(n for n in needed if n not in copied)
    assert not missing, (
        f"the Dockerfile does not COPY {missing}; the image would die with "
        f"ModuleNotFoundError on every invocation")


@check("the image carries no .env, no test suite and no fixtures")
def test_dockerignore_excludes_secrets():
    path = os.path.join(HERE, ".dockerignore")
    assert os.path.exists(path), ".dockerignore is missing"
    ignored = {l.strip() for l in open(path, encoding="utf-8")
               if l.strip() and not l.startswith("#")}
    # A .env baked into an image is a credential published to everyone who
    # can pull it.
    for needed in (".env", "smoke_test.py"):
        assert any(needed in pattern for pattern in ignored), (
            f"{needed} is not excluded from the Docker build context")


@check("CI calls the shipped credential check rather than reimplementing it")
def test_ci_invokes_the_shipped_check():
    # Two sources of truth, one dead and one holed, is how a sibling family
    # ended up with an inline grep that matched only ws:// and would have let
    # an http://user:pass@ credential sail past CI, next to a shipped script
    # nothing ran.
    wf = os.path.join(HERE, ".github", "workflows", "tests.yml")
    assert os.path.exists(wf), "tests.yml is missing"
    text = open(wf, encoding="utf-8").read()
    assert "ci_checks.py" in text, (
        "tests.yml must invoke .github/ci_checks.py, not reimplement it")


@check("sample_output columns match the dataclass, with no fabrication")
def test_sample_output():
    for name, cls in (("sample_output.json", ow.Campaign),):
        path = os.path.join(HERE, name)
        assert os.path.exists(path), f"{name} is missing"
        rows = json.load(open(path, encoding="utf-8"))
        assert rows, f"{name} is empty"
        expected = [f.name for f in fields(cls)]
        for row in rows:
            assert list(row) == expected, (
                f"{name} columns drifted from {cls.__name__}")
        blob = json.dumps(rows).lower()
        for marker in ("lorem ipsum", "example.com", "foo bar", "test test",
                       "placeholder"):
            assert marker not in blob, f"{name} looks fabricated: {marker}"
    csv_path = os.path.join(HERE, "sample_output.csv")
    assert os.path.exists(csv_path)
    with open(csv_path, encoding="utf-8") as f:
        header = next(csv.reader(f))
    assert header == [f.name for f in fields(ow.Campaign)]


@check("the README's numeric claims match the artefacts on disk")
def test_readme_numbers():
    path = os.path.join(HERE, "README.md")
    if not os.path.exists(path):
        return
    text = open(path, encoding="utf-8").read()
    rows = json.load(open(os.path.join(HERE, "sample_output.json"),
                          encoding="utf-8"))
    # The sample's own row count is the one figure that MUST agree, because
    # it is a property of a file in this repo rather than of a live site.
    m = re.search(r"sample_output\.json[^\n]*?(\d+)\s+rows", text)
    if m:
        assert int(m.group(1)) == len(rows), (
            f"README says {m.group(1)} sample rows, the file has {len(rows)}")


# ---------------------------------------------------------------------------
# Concurrency, with the browser stubbed out
# ---------------------------------------------------------------------------

@check("concurrency policy: refused with CDP, warned without a pool")
def test_concurrency_policy():
    assert page_flow.concurrency_warning(1, False, None) is None
    # Refused: the Scraping Browser allows one live connection per profile.
    msg = page_flow.concurrency_warning(4, True, "ws://x")
    assert msg and "profile_locked" in msg
    # Warned, not refused: N workers from one address is the user's call.
    msg = page_flow.concurrency_warning(4, False, None)
    assert msg and "proxy" in msg.lower()
    assert page_flow.concurrency_warning(4, True, None) is None


@check("each concurrency worker owns its own exit, with no shared lock")
def test_worker_pools_are_independent():
    eng = _engine("playwright_scraper")
    if eng is None:
        return
    pool = proxy_pool.ProxyPool(["http://a:1", "http://b:2", "http://c:3"])
    firsts = [eng._worker_pool(pool, i).current for i in range(3)]
    assert len(set(firsts)) == 3, (
        f"workers must start on DIFFERENT exits, got {firsts}")
    # A copy, not the list itself: two threads sharing one mutable list is
    # the bug that makes concurrency stop being worth it.
    w = eng._worker_pool(pool, 1)
    w.advance("test")
    assert pool.current == "http://a:1", "a worker moved the parent pool"


@check("pages are addressed independently even though URLs are not")
def test_independent_addressing():
    # The two functions say different things on purpose: one is about URLs,
    # the other about whether the concurrency model applies at all.
    assert page_flow.listing_is_url_addressable() is False
    assert page_flow.pages_are_independently_addressable("search") is True
    assert page_flow.pages_are_independently_addressable("campaign") is False


@check("the search API client raises rather than returning an empty result")
def test_search_api_client_raises():
    # A function that returns {} on failure is indistinguishable from a
    # genuinely empty page, and this repo's stop condition is "this page
    # added no new ids" -- so a silent {} would end a run early and report it
    # as complete.
    for name in ENGINES:
        eng = _engine(name)
        if eng is None:
            continue
        assert hasattr(eng, "SearchAPIError"), name
        src = inspect.getsource(eng.fetch_search_page)
        assert "raise SearchAPIError" in src, (
            f"{name}.fetch_search_page must raise, not return a default")
        assert "return {}" not in src, name


@check("a parse failure raises rather than reporting an empty listing")
def test_parser_raises_on_wrong_shape():
    try:
        pp.parse_search_api({"nope": 1}, page_index=0, sort="default")
    except ValueError:
        pass
    else:
        raise AssertionError("a non-search payload must raise, not return []")
    try:
        pp.parse_search_api({"projects": {}}, page_index=0, sort="default")
    except ValueError:
        pass
    else:
        raise AssertionError("a payload with no pagedItems must raise")


@check("a malformed inline state raises; an absent one returns None")
def test_inline_state_errors():
    # Different problems, and a caller that cannot tell them apart will
    # "handle" a site change by silently producing no rows.
    assert pp.extract_inline_state("<html></html>") is None
    try:
        pp.extract_inline_state("<script>window.__INITIAL_STATE__ = {oops;</script>")
    except ValueError:
        pass
    else:
        raise AssertionError("malformed state must raise")


@check("a campaign page and the search API resolve category identically")
def test_category_resolution():
    assert pp.category_name({"projectCategory": 11,
                             "name": "Board & card games"}) == "Board & card games"
    assert pp.category_name(50) == "Productivity"
    assert pp.category_name(99999) is None, "an unknown code is not guessed"
    assert pp.category_name(None) is None
    assert pp.category_name(True) is None, "a bool is not a category code"


@check("engines import cleanly, or record a visible skip")
def test_engines_import():
    for name in ENGINES:
        _engine(name)
    # Nothing is asserted about how many imported: the point is that the
    # skip is RECORDED and printed. "skipped, engine absent" reads
    # identically to a real import error, so CI's engine-smoke job installs
    # each engine in its own virtualenv and fails if its group skips.


@check("every engine maps its states to the same stop reasons")
def test_stop_reason_parity():
    # Three copies of a triage across three engines drift, and the drift is
    # silent: one engine reporting exit 3 where its twin reports exit 4 on
    # the same page.
    maps = {}
    for name in ENGINES:
        src = open(os.path.join(HERE, f"{name}.py"), encoding="utf-8").read()
        found = dict(re.findall(r'"(empty|not_found|captcha|blocked|unpainted)":\s*"([a-z_]+)"', src))
        maps[name] = found
    ref = maps["playwright_scraper"]
    assert ref, "could not find the state->stop_reason map"
    for name, m in maps.items():
        assert m == ref, f"{name} maps states differently: {m} != {ref}"


@check("finish_run is the only place an exit code is decided")
def test_single_exit_mapping():
    for name in ENGINES:
        src = open(os.path.join(HERE, f"{name}.py"), encoding="utf-8").read()
        # An engine may return 2 (usage) and EXIT_REMOTE_API_ERROR directly;
        # everything else must go through finish_run.
        assert "finish_run(" in src, name
        for literal in ("return 3", "return 4", "return 6"):
            assert literal not in src, (
                f"{name} returns a bare {literal}; the status/exit mapping "
                f"belongs in output_writer.finish_run so it cannot drift")


@check("the supported locales come from the site's own hreflang set")
def test_locales():
    assert pp.LOCALES == ("en", "it", "fr", "de", "pl", "es", "cs", "pt", "zh")
    assert pp.CANONICAL_HOST == "www.indiegogo.com"
    for loc in pp.LOCALES:
        u = f"https://www.indiegogo.com/{loc}/projects/creator/slug"
        assert pp.is_project_url(u), u
        assert pp.slug_from_url(u) == "slug"
        assert pp.creator_from_url(u) == "creator"


@check("the legacy no-locale campaign URL is still recognised")
def test_legacy_project_url():
    # /projects/<slug> with no creator segment is the pre-Gamefound shape;
    # the site still redirects it, so a user pasting one must not be told it
    # is not a campaign URL.
    u = "https://www.indiegogo.com/projects/gpd-win-max-3-handheld-gaming-laptop"
    assert pp.is_project_url(u), u
    assert pp.slug_from_url(u) == "gpd-win-max-3-handheld-gaming-laptop"


@check("a gamefound-hosted row is labelled, never fetched as an Indiegogo page")
def test_offplatform_rows_are_labelled():
    # /en/projects/<creator>/<slug> answers 404 on indiegogo.com for these.
    sp = pp.parse_search_api(SEARCH_API_PAYLOAD, page_index=0, sort="default")
    off = [r for r in sp.rows if r.platform == "gamefound"]
    assert off, "the fixture should contain at least one off-platform row"
    for r in off:
        assert "gamefound.com" in r.url, r.url


@check("category_from_url reads the site's own filter parameter")
def test_category_from_url():
    u = ("https://www.indiegogo.com/en/projects/search"
         "?projectCatalogCategories=BoardAndCardGames")
    assert pp.category_from_url(u) == "BoardAndCardGames"
    # None rather than "all": a null means "the site was not asked to
    # filter", not "filtered to everything".
    assert pp.category_from_url("https://www.indiegogo.com/en/projects/search") is None


@check("a thin page is logged, never retried")
def test_thin_page_is_informational():
    assert page_flow.is_thin_page(3, 24) is True
    assert page_flow.is_thin_page(24, 24) is False
    assert page_flow.is_thin_page(5, 0) is False
    src = inspect.getsource(page_flow)
    assert "is_thin_page" in src
    # It must not appear in any retry decision.
    for name in ENGINES:
        esrc = open(os.path.join(HERE, f"{name}.py"), encoding="utf-8").read()
        for line in esrc.splitlines():
            if "is_thin_page" in line:
                assert "retry" not in line.lower(), line


@check("no source file uses syntax newer than the oldest Python we claim")
def test_oldest_python_syntax():
    # CI runs a 3.9 leg, and pyproject declares requires-python. Claiming 3.9
    # without testing it is how a walrus operator ships -- and this repo
    # INHERITED exactly that: a `int | None` annotation (PEP 604, 3.10+) in
    # tools/browser_profile_client.py, copied from a sibling, which raises a
    # TypeError at import on 3.9. CI's 3.9 leg would only have caught it for
    # the files it happens to invoke.
    #
    # Walked over the AST's ANNOTATION nodes rather than pattern-matched over
    # the text: the first version of this check was a regex and its first
    # finding was `(?:ws|wss)` inside a credential regex -- a check that
    # cries wolf on a string literal is a check people learn to skip.
    import glob
    offenders = []
    for path in (glob.glob(os.path.join(HERE, "*.py"))
                 + glob.glob(os.path.join(HERE, "tools", "*.py"))
                 + glob.glob(os.path.join(HERE, "tests", "*.py"))
                 + glob.glob(os.path.join(HERE, ".github", "*.py"))):
        src = open(path, encoding="utf-8").read()
        tree = ast.parse(src)
        if any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
               and any(a.name == "annotations" for a in n.names)
               for n in ast.walk(tree)):
            continue          # annotations are strings; never evaluated
        rel = os.path.relpath(path, HERE)

        def flag(node, what):
            offenders.append(f"{rel}:{getattr(node, 'lineno', '?')} {what}")

        def check_annotation(node):
            # A quoted annotation is a string and is never evaluated.
            if node is None or isinstance(node, ast.Constant):
                return
            for sub in ast.walk(node):
                if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
                    flag(sub, "PEP 604 annotation (X | Y), 3.10+")

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                check_annotation(node.returns)
                a = node.args
                for arg in (list(a.args) + list(a.kwonlyargs)
                            + list(a.posonlyargs)
                            + [x for x in (a.vararg, a.kwarg) if x]):
                    check_annotation(arg.annotation)
            elif isinstance(node, ast.AnnAssign):
                check_annotation(node.annotation)
            elif isinstance(node, getattr(ast, "Match", ())):
                flag(node, "match statement, 3.10+")
    assert not offenders, (
        "syntax newer than the oldest Python this repo claims:\n  "
        + "\n  ".join(offenders))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    print(f"Running {len(CHECKS)} checks for indiegogo-scraper\n")
    for run in CHECKS:
        run()

    if SKIPPED:
        print("\nSKIPPED (engine libraries absent — this is expected offline,")
        print("and CI's engine-smoke job fails if the installed one skips):")
        for s in sorted(set(SKIPPED)):
            print(f"  - {s}")

    print(f"\n{len(PASSED)} passed, {len(FAILURES)} failed, "
          f"{len(set(SKIPPED))} skip(s) recorded")
    if FAILURES:
        print("\nFAILURES:")
        for name, why in FAILURES:
            print(f"  FAIL  {name}\n        {why}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
