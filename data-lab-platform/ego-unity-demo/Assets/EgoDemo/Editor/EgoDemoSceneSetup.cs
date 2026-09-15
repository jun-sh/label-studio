#if UNITY_EDITOR
using System.IO;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Ego.Demo.Editor
{
    public static class EgoDemoSceneSetup
    {
        const string ScenePath = "Assets/EgoDemo/Scenes/EgoDemo.unity";

        [MenuItem("Ego Demo/Create Demo Scene")]
        public static void CreateDemoScene()
        {
            Directory.CreateDirectory("Assets/EgoDemo/Scenes");
            var scene = EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);
            if (Object.FindObjectOfType<EgoDemoRuntimeBootstrap>() == null)
                new GameObject("EgoDemo", typeof(EgoDemoRuntimeBootstrap));

            EditorSceneManager.SaveScene(scene, ScenePath);
            AssetDatabase.Refresh();
            EditorUtility.DisplayDialog(
                "Ego Demo",
                $"Scene saved:\n{ScenePath}\n\nPress Play (UI builds automatically).",
                "OK");
        }
    }
}
#endif
