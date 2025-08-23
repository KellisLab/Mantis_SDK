from .client import MantisClient
from .config import ConfigurationManager

COOKIE = """_hp2_id.2065608176=%7B%22userId%22%3A%224946794238876371%22%2C%22pageviewId%22%3A%225090775825011798%22%2C%22sessionId%22%3A%226112583324479946%22%2C%22identity%22%3Anull%2C%22trackerVersion%22%3A%224.0%22%7D; _ga=GA1.2.1963620237.1739759925; _ga_HWFLFTST95=GS1.2.1739759925.1.0.1739759925.0.0.0; _ga_RP0185XJY9=GS1.1.1739759924.1.1.1739759939.0.0.0; _ga_KXD10J0YGJ=GS1.2.1743034083.1.0.1743034083.0.0.0; _gid=GA1.2.850294094.1743266889; _ga_V6GE2CH3Y2=GS1.2.1743266888.1.0.1743266888.0.0.0; __Host-next-auth.csrf-token=017bb2ae58a67f40a72ca915cebf2a8b631ccb74ca9ff37f6732186f8c1c1fd3%7Ced9c8866834d5822dedd5cf4029e73cf59de46872676fdbada264674dff0d195; __Secure-next-auth.callback-url=https%3A%2F%2Fmantisdev.csail.mit.edu%2Fhome%2F; __Secure-next-auth.session-token=eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..9fXxikZwSDiSVBKG.4yM4Tm1A1sx1Ecx_qAQkdatgDzClYT5rcjwmb0UsSbXLgInw5GUOZ8umQSSA0I2q--lcyWa66rLOhHX7uC8xlP8IfSHlis_nU3QYIv1ZX4tqdiPoMCK437d5NMjdDW3MILlDh5zfvtIvZjcxOFHGEJ3Eq6jfxfYX7_DKjkPoJK2aL9DG-gPT_iFp5eJxbjvtFHAx9AXNEs-slab0bRcBSXJSY7NdfFRVWpe4LAvAI7jaz5UDwC0QcIG9e472TmHFOuDsnDnVzYYPBBcItnA5V-xsS2u1oykLVD5H4kK82oEW-NSrZRfrYtkGtvWkngi95XuTrxKuVDtFabcXmVykaHQMX6TI5aGwS0aJUUjwaC6LS5nz5vIOInj-WA.TYtz8sFXZU6mLqVXpZymaw; sessionid=gntq667ntgisa9h43gom5j52wox14te5; ph_phc_xKneBiNcXuoXtSlj6ZGCwvlVtHsjuQ8vAAhax5GL0VM_posthog=%7B%22distinct_id%22%3A%22lbvoros%40gmail.com%22%2C%22%24sesid%22%3A%5B1743285704760%2C%220195e3ec-a0d3-7bc0-97f9-25f0cad39bfc%22%2C1743285690579%5D%2C%22%24epp%22%3Atrue%2C%22%24initial_person_info%22%3A%7B%22r%22%3A%22%24direct%22%2C%22u%22%3A%22https%3A%2F%2Fmantisdev.csail.mit.edu%2Fsignin%2F%3FcallbackUrl%3Dhttps%253A%252F%252Fmantisdev.csail.mit.edu%252F%22%7D%7D"""

config = ConfigurationManager()
config.update({
    "host": "https://mantisdev.csail.mit.edu",
    "domain": "mantisdev.csail.mit.edu",
})

client = MantisClient("/api/proxy/",
                      COOKIE,
                      config)

