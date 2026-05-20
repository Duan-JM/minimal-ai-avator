# -*- coding: utf-8 -*-
"""
@author:XuMing(xuming624@qq.com)
@description: 测试GPU服务的简单脚本
"""
import requests
import numpy as np
import cv2
import base64
import time

def test_health(base_url):
    """测试健康检查"""
    print("\n=== 测试健康检查 ===")
    resp = requests.get(f"{base_url}/health")
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.json()}")
    return resp.status_code == 200

def test_session(base_url, session_id):
    """测试会话初始化"""
    print("\n=== 测试会话初始化 ===")
    
    # 创建测试图片（256x256的黑色图片，与Wav2Lip V2要求一致）
    test_img = np.zeros((256, 256, 3), dtype=np.uint8)
    _, buffer = cv2.imencode('.png', test_img)
    img_b64 = base64.b64encode(buffer).decode('utf-8')
    
    # 创建3张测试图片
    face_imgs = [img_b64, img_b64, img_b64]
    
    resp = requests.post(
        f"{base_url}/session/init",
        json={
            'session_id': session_id,
            'face_imgs': face_imgs
        }
    )
    
    print(f"Status: {resp.status_code}")
    print(f"Response: {resp.json()}")
    return resp.status_code == 200

def test_inference(base_url, session_id):
    """测试推理"""
    print("\n=== 测试批量推理 ===")
    
    # 创建测试mel_batch (batch_size=2, 80, 16)
    mel_batch = np.random.rand(2, 80, 16).astype(np.float32)
    mel_bytes = mel_batch.tobytes()
    mel_b64 = base64.b64encode(mel_bytes).decode('utf-8')
    
    start = time.time()
    resp = requests.post(
        f"{base_url}/inference/batch",
        json={
            'session_id': session_id,
            'mel_batch': mel_b64,
            'mel_shape': list(mel_batch.shape),
            'face_indices': [0, 1]  # 使用face 0和1
        }
    )
    elapsed = time.time() - start
    
    print(f"Status: {resp.status_code}")
    if resp.status_code == 200:
        result = resp.json()
        print(f"Batch size: {result['batch_size']}")
        print(f"Infer time: {result['infer_time']:.4f}s")
        print(f"Total time: {result['total_time']:.4f}s")
        print(f"Network time: {elapsed:.4f}s")
        
        batch_bytes = base64.b64decode(result['batch_data'])
        batch_shape = tuple(result['batch_shape'])
        frames = np.frombuffer(batch_bytes, dtype=np.uint8).reshape(batch_shape)
        print(f"✓ Frames received (batch): {frames.shape}")
    else:
        print(f"Error: {resp.text}")
        return False
    
    return resp.status_code == 200

def test_close_session(base_url, session_id):
    """测试关闭会话"""
    print("\n=== 测试关闭会话 ===")
    resp = requests.post(
        f"{base_url}/session/close",
        json={'session_id': session_id}
    )
    print(f"Status: {resp.status_code}")
    if resp.status_code == 200:
        print(f"Response: {resp.json()}")
    else:
        print(f"Error: {resp.text}")
    return resp.status_code == 200

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', type=str, default='http://localhost:8080', help='GPU服务器地址')
    args = parser.parse_args()
    
    base_url = args.url.rstrip('/')
    session_id = "test_session_123"
    
    print(f"测试GPU服务: {base_url}")
    print("=" * 50)
    
    # 运行测试
    tests = [
        ("健康检查", lambda: test_health(base_url)),
        ("会话初始化", lambda: test_session(base_url, session_id)),
        ("批量推理", lambda: test_inference(base_url, session_id)),
        ("关闭会话", lambda: test_close_session(base_url, session_id)),
    ]
    
    results = []
    for name, test_func in tests:
        try:
            success = test_func()
            results.append((name, success))
        except Exception as e:
            print(f"错误: {e}")
            results.append((name, False))
    
    # 打印结果
    print("\n" + "=" * 50)
    print("测试结果:")
    print("=" * 50)
    for name, success in results:
        status = "✓" if success else "✗"
        print(f"{status} {name}")
    
    all_passed = all(success for _, success in results)
    print("=" * 50)
    if all_passed:
        print("✓ 所有测试通过！")
    else:
        print("✗ 部分测试失败")
    
    return 0 if all_passed else 1

if __name__ == '__main__':
    exit(main())
