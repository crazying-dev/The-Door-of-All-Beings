using UnityEngine;

namespace TheDoorOfBings.Core
{
    /// <summary>
    /// 单例基类
    /// </summary>
    public class Singleton : MonoBehaviour
    {
        protected static Singleton _instance;

        public static Singleton Instance
        {
            get
            {
                if (_instance == null)
                {
                    _instance = FindObjectOfType<Singleton>();
                    if (_instance == null)
                    {
                        GameObject go = new GameObject(typeof(Singleton).Name);
                        _instance = go.AddComponent<Singleton>();
                    }
                }
                return _instance;
            }
        }

        protected virtual void Awake()
        {
            if (_instance != null && _instance != this)
            {
                Destroy(gameObject);
                return;
            }
            _instance = this;
            DontDestroyOnLoad(gameObject);
        }
    }
}