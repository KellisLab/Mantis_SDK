from client import MantisClient, SpacePrivacy, DataType
import time
import pandas as pd
import random

cookie = "sessionid=q1xtub1gf2ubi70b0lpfs8xxlrxqlpo5; next-auth.callback-url=http%3A%2F%2Flocalhost%3A3000; next-auth.csrf-token=bbed8300554e62abdb2fa557868db6c43d00b30b7e367c791c90ef29d7adfeb9%7C39eb4e0023338c4806ddc1e3c7132a55ea0f1280039d88197478874607cfeabe; next-auth.session-token=eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..ktmzhArKOcFUH4Nk.iarp_a1z5b-e4xzsDoLIp5K3KynndVnZRNNLvxxY3PW9-Jb13LkU9gn5Bnpq6seMJXQ-hyCM0kXucFkrtmLkmipeYcB4ta_W7DAZjuosbNYm51kTZ0D6GcAZqgE6ngZBOPtRKODQpTV6f-0ynuEE9iKHX0xgEvDDAJ5gzYp7B_X7zXwMBq3b4h63C4T0O_v1uxRPBVVYwweaDAnYfeva4QsiIpjlLg2P5P0Xlccu15OTwjN9zJptH4ArXa3dsiAvU0yDcN-ps35kGJsJh73fwnpd0kg_tens-EK0vdz834hQlop0PSQjWUqwIg.q1-TG7fujInKozTr9_oV3A; ph_phc_xKneBiNcXuoXtSlj6ZGCwvlVtHsjuQ8vAAhax5GL0VM_posthog=%7B%22distinct_id%22%3A%22lbvoros%40gmail.com%22%2C%22%24sesid%22%3A%5B1738366650436%2C%220194be9e-db8b-78f8-8e6f-9807a4aaf4e2%22%2C1738364869515%5D%2C%22%24epp%22%3Atrue%2C%22%24initial_person_info%22%3A%7B%22r%22%3A%22%24direct%22%2C%22u%22%3A%22http%3A%2F%2Flocalhost%3A3000%2Fsignin%2F%3FcallbackUrl%3Dhttp%253A%252F%252Flocalhost%253A3000%252F%22%7D%7D"

client = MantisClient("/api/proxy/", cookie)