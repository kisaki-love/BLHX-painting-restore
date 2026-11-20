import unittest
from types import SimpleNamespace
from cubism_motion import Bindings, convert, crc


def clip(keys, values, duration=1):
    bindings = [dict(path=p, attribute=a, typeID=114, script={"m_PathID":s}) for p,a,s in keys]
    data = {"m_StreamedClip":{"curveCount":0,"data":[]},
            "m_DenseClip":{"m_CurveCount":0,"m_FrameCount":0,"m_SampleArray":[]},
            "m_ConstantClip":{"data":values}}
    return SimpleNamespace(read_typetree=lambda: {
        "m_SampleRate":30, "m_ClipBindingConstant":{"genericBindings":bindings},
        "m_MuscleClip":{"m_StartTime":0,"m_StopTime":duration,"m_Clip":data}})


class MotionBindingsTest(unittest.TestCase):
    def test_override_and_model_effect_use_correct_columns(self):
        mouth = (1, crc("Value"), 1)
        controller = (0, crc("MouthOpening"), 3)
        eye = (0, crc("EyeOpening"), 4)
        table = Bindings({mouth:("Parameter","Mouth")}, {},
                         {controller:[("Parameter","Mouth")], eye:[("Model","EyeBlink")]},
                         {"EyeBlink":["EyeL","EyeR"]}, {}, {}, set())
        m, skipped = convert(clip([mouth,controller,eye],[0.1,0.7,0.8]), table)
        self.assertFalse(skipped)
        self.assertEqual([(c["Target"],c["Id"],c["Segments"][1]) for c in m["Curves"]],
                         [("Model","EyeBlink",0.8),("Parameter","Mouth",0.7)])
        self.assertEqual(m["Meta"]["TotalPointCount"], 4)

    def test_absent_requires_complete_model_and_missing_path(self):
        prop = crc("Opacity")
        keys = [(1,prop,20),(2,prop,20),(3,prop,21),(4,crc("Other"),20)]
        table = Bindings({}, {20:"CubismPart",21:"CubismPart"}, {}, {}, {}, {20:prop}, {2})
        m, diagnostics = convert(clip(keys,[1,1,1,1]),table)
        self.assertIsNone(m)
        self.assertEqual([x["path_crc"] for x in diagnostics["预制体路径不存在 CubismPart"]],[1])
        self.assertEqual(len(diagnostics["CubismPart"]),3)

    def test_empty_clip_creates_no_motion(self):
        self.assertEqual(convert(clip([],[],0),Bindings({}, {}, {}, {}, {}, {}, set())),(None,{"empty":1}))
