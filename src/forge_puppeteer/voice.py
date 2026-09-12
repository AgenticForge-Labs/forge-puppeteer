from __future__ import annotations
from dataclasses import dataclass,field
from typing import Any,Literal,Protocol

AudioFormat=Literal['mp3','opus','aac','flac','wav','pcm']
class VoiceProvider(Protocol):
    async def speak(self,text:str,*,audio_uri:str|None=None,model_alias:str|None=None,voice:str|None=None,response_format:AudioFormat|None=None,speed:float|None=None)->None: ...
    async def stop(self)->None: ...

@dataclass
class MockVoiceProvider:
    utterances:list[str]=field(default_factory=list); requests:list[dict[str,Any]]=field(default_factory=list); stop_count:int=0
    async def speak(self,text:str,*,audio_uri:str|None=None,model_alias:str|None=None,voice:str|None=None,response_format:AudioFormat|None=None,speed:float|None=None):
        self.utterances.append(text); self.requests.append({'text':text,'audio_uri':audio_uri,'model_alias':model_alias,'voice':voice,'response_format':response_format,'speed':speed})
    async def stop(self): self.stop_count+=1

@dataclass
class SilentVoiceProvider:
    stop_count:int=0
    async def speak(self,text:str,**kwargs): return None
    async def stop(self): self.stop_count+=1
