from __future__ import annotations
import json
from collections.abc import Iterable,Iterator
from dataclasses import dataclass,field
from math import isfinite
from pathlib import Path
from typing import Protocol,runtime_checkable

@dataclass(frozen=True,slots=True)
class CameraCapabilities:
    video:bool=True; snapshots:bool=True; manual_focus:bool=False; manual_exposure:bool=False; optical_zoom:bool=False; electronic_zoom:bool=False; native_pan_tilt:bool=False
@dataclass(frozen=True,slots=True)
class CameraStatus:
    is_open:bool; healthy:bool; detail:str=''
@runtime_checkable
class CaptureDevice(Protocol):
    @property
    def camera_id(self)->str: ...
    @property
    def capabilities(self)->CameraCapabilities: ...
    def open(self)->None: ...
    def close(self)->None: ...
    def status(self)->CameraStatus: ...

class CameraCollection:
    def __init__(self,cameras:Iterable[CaptureDevice]):
        self._cameras={}
        for camera in cameras:
            if camera.camera_id in self._cameras: raise ValueError(f'Duplicate camera id: {camera.camera_id}')
            self._cameras[camera.camera_id]=camera
    def __len__(self): return len(self._cameras)
    def __iter__(self)->Iterator[CaptureDevice]: return iter(self._cameras.values())
    def ids(self): return tuple(self._cameras)
    def get(self,camera_id):
        try: return self._cameras[camera_id]
        except KeyError as exc: raise ValueError(f'Unknown camera id: {camera_id}') from exc
    def open(self,camera_ids:Iterable[str]):
        opened=[]
        try:
            for cid in dict.fromkeys(camera_ids): self.get(cid).open(); opened.append(cid)
        except Exception as error:
            try: self.close(reversed(opened))
            except Exception as cleanup: error.add_note(f'Camera cleanup failed: {cleanup!r}')
            raise
        return tuple(opened)
    def close(self,camera_ids:Iterable[str]):
        first=None
        for cid in camera_ids:
            try: self.get(cid).close()
            except Exception as exc:
                if first is None: first=exc
        if first: raise first
    def open_all(self): return self.open(self._cameras)
    def close_all(self): self.close(reversed(tuple(self._cameras)))

@dataclass(frozen=True,slots=True)
class AxisRange:
    minimum:float; maximum:float; step:float|None=None
    def __post_init__(self):
        if not all(isfinite(v) for v in (self.minimum,self.maximum)): raise ValueError('axis range values must be finite')
        if self.minimum>self.maximum: raise ValueError('axis range minimum must not exceed maximum')
        if self.step is not None and (not isfinite(self.step) or self.step<=0): raise ValueError('axis range step must be finite and positive')
@dataclass(frozen=True,slots=True)
class CameraMotionCapabilities:
    pan:bool=False; tilt:bool=False; zoom:bool=False; absolute:bool=False; relative:bool=False; stop:bool=False
@dataclass(frozen=True,slots=True)
class CameraMotionPosition:
    pan:float|None=None; tilt:float|None=None; zoom:float|None=None
    def __post_init__(self):
        for name,value in (('pan',self.pan),('tilt',self.tilt)):
            if value is not None and (not isfinite(value) or not -1<=value<=1): raise ValueError(f'{name} must be between -1.0 and 1.0')
        if self.zoom is not None and (not isfinite(self.zoom) or not 0<=self.zoom<=1): raise ValueError('zoom must be between 0.0 and 1.0')
@dataclass(frozen=True,slots=True)
class CameraMotionLimits:
    pan:AxisRange|None=None; tilt:AxisRange|None=None; zoom:AxisRange|None=None
@dataclass(slots=True)
class CameraMotionCalibration:
    controller_id:str; neutral:CameraMotionPosition; limits:CameraMotionLimits; presets:dict[str,CameraMotionPosition]=field(default_factory=dict)
    def save_preset(self,name,position):
        key=name.strip()
        if not key: raise ValueError('preset name must be non-empty')
        self.presets[key]=position
@runtime_checkable
class CameraMotionController(Protocol):
    @property
    def controller_id(self)->str: ...
    @property
    def capabilities(self)->CameraMotionCapabilities: ...
    def connect(self)->None: ...
    def disconnect(self)->None: ...
    def position(self)->CameraMotionPosition: ...
    def limits(self)->CameraMotionLimits: ...
    def move_absolute(self,position:CameraMotionPosition)->None: ...
    def move_relative(self,delta:CameraMotionPosition)->None: ...
    def stop(self)->None: ...

def _pos(p): return {'pan':p.pan,'tilt':p.tilt,'zoom':p.zoom}
def _rng(r): return None if r is None else {'minimum':r.minimum,'maximum':r.maximum,'step':r.step}
def save_calibration(calibration:CameraMotionCalibration,path:Path):
    path.parent.mkdir(parents=True,exist_ok=True); path.write_text(json.dumps({'version':2,'controller_id':calibration.controller_id,'neutral':_pos(calibration.neutral),'limits':{'pan':_rng(calibration.limits.pan),'tilt':_rng(calibration.limits.tilt),'zoom':_rng(calibration.limits.zoom)},'presets':{k:_pos(v) for k,v in sorted(calibration.presets.items())}},indent=2,sort_keys=True)+'\n')
def _position(v): return CameraMotionPosition(pan=v.get('pan'),tilt=v.get('tilt'),zoom=v.get('zoom'))
def _range(v): return None if v is None else AxisRange(minimum=float(v['minimum']),maximum=float(v['maximum']),step=float(v['step']) if v.get('step') is not None else None)
def load_calibration(path:Path)->CameraMotionCalibration:
    data=json.loads(path.read_text())
    if data.get('version')!=2: raise ValueError('unsupported camera calibration version; recapture calibration')
    limits=data['limits']; return CameraMotionCalibration(controller_id=str(data['controller_id']),neutral=_position(data['neutral']),limits=CameraMotionLimits(pan=_range(limits.get('pan')),tilt=_range(limits.get('tilt')),zoom=_range(limits.get('zoom'))),presets={str(k):_position(v) for k,v in data.get('presets',{}).items()})
