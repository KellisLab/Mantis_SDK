from . import config

config.HOST = "https://mantisdev.csail.mit.edu"
config.DOMAIN = "mantisdev.csail.mit.edu"

from .client import MantisClient, SpacePrivacy, DataType, ReducerModels, AIProvider

import time
import pandas as pd
import random

cookie = "MIT_PressMachineID=638695455057712972; fpestid=y_jCLhNDgF9Sl8XTsHV4RVkCavCXEfFAye1gwdskq0XB1Ld-t2d3Kfct4Sziqmm4z37ANQ; _gcl_au=1.1.1818488075.1733948708; _ga=GA1.2.1626376916.1733948708; _cc_id=87e78069c8ef999b8a9bee266d763eb; _fbp=fb.1.1733948708594.560146927926962738; hum_mit_visitor=626c6e0b-d291-445b-ac5f-ef3af434480b; hum_mit_synced=true; _ga_VJ81RKXDL1=GS1.1.1733948708.1.0.1733948744.24.0.0; sessionid=wviy8yk3w6jn9oy994rwvsa6ucrwv3sv; ph_phc_twLBfXCcnUBL6puODlvhWgNBNBMXKJAndCeqb957mO9_posthog=%7B%22distinct_id%22%3A%22lbvoros%40gmail.com%22%2C%22%24sesid%22%3A%5B1736968586799%2C%2201946b50-b62e-78ba-8906-74086c0a5583%22%2C1736967239214%5D%2C%22%24epp%22%3Atrue%2C%22%24initial_person_info%22%3A%7B%22r%22%3A%22%24direct%22%2C%22u%22%3A%22https%3A%2F%2Fmantisdev.csail.mit.edu%2Fhome%2F%22%7D%7D; __Host-next-auth.csrf-token=ebc19cfc418148c84d46648e664750bc605bc5f25d3cfa9e21b29465fd5805ce%7Cfe5325441df4c8e5694880cf3fa9e7b8de933ad2a233331ade8a679e04cb3cba; __Secure-next-auth.callback-url=https%3A%2F%2Fmantisdev.csail.mit.edu; ph_phc_xKneBiNcXuoXtSlj6ZGCwvlVtHsjuQ8vAAhax5GL0VM_posthog=%7B%22distinct_id%22%3A%22lbvoros%40gmail.com%22%2C%22%24sesid%22%3A%5B1739059094412%2C%220194e7ff-bec1-7c52-a0fc-125ed9f1c133%22%2C1739059084993%5D%2C%22%24epp%22%3Atrue%2C%22%24initial_person_info%22%3A%7B%22r%22%3A%22%24direct%22%2C%22u%22%3A%22https%3A%2F%2Fmantisdev.csail.mit.edu%2Fhome%2F%22%7D%7D; __Secure-next-auth.session-token=eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..ilCrmFuIK1ISUV2-.saCM2-oJfnOs7y8wLP6fGDV3gAgb8Ql0DgKxQi49oyufL6AovHL8Ry9rjDYnK6bllj1_a_XnsXFyrlC6ouHoOp7VvvCUR4JJsubvZvwhBLNxcyQXfd5KiRhiqL2q08egI-Kek36Ro2-KLUm2boOCLV3Kh6CtPcFEKB8ctYRtYGKDEe2S71ASKGzZizIaWT04c80R_sj4vKSHxds3ErNuOd2owf2dgu52VqviyyMxomw-w7o7DphIFozESg8_PaTPvHSW_fOAy2NueeAIHyBIU3-iV4SPBTThMmFmNjfLOCbKfQ98NohijxEljtFc3n5buyucZcujQqekbedoB8xmNaXiySFlZTm_Fnc7_KZ1CKEv1p2gmdCYQT4A6Q.aigSxBoaKLAXF9-xunSpxQ"

mantis = MantisClient("/api/proxy/", cookie)

# Create space with provided parameters 
space_response = mantis.create_space(
    space_name="Selecting UMAP Variations Pt 5",
    data="/home/lvoros/Mantis/SDK/StockDataSmall.csv",
    data_types={
        "Name": DataType.Title,
        "Market Cap": DataType.Numeric,
        "Description": DataType.Semantic,    
    },
    reducer=ReducerModels.UMAP,
    privacy_level=SpacePrivacy.PRIVATE,
    ai_provider=AIProvider.OpenAI
)

print (space_response)