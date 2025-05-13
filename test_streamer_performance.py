#!/usr/bin/env python3
import subprocess
import time
import psutil
import requests
import json
from datetime import datetime
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('streamer_performance.log'),
        logging.StreamHandler()
    ]
)

class StreamerPerformanceTest:
    def __init__(self):
        self.start_time = None
        self.end_time = None
        self.process = None
        self.metrics = {
            'cpu_usage': [],
            'memory_usage': [],
            'fps': [],
            'bitrate': [],
            'latency': []
        }

    def start_test(self):
        """启动测试"""
        self.start_time = time.time()
        logging.info("开始性能测试...")
        
        # 启动流媒体进程
        self.process = subprocess.Popen(
            ['kvmd', '--run'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        # 等待服务启动
        time.sleep(5)
        
        # 开始收集指标
        self._collect_metrics()

    def _collect_metrics(self):
        """收集性能指标"""
        try:
            while True:
                # 收集CPU和内存使用率
                cpu_percent = psutil.cpu_percent(interval=1)
                memory_percent = psutil.virtual_memory().percent
                
                # 获取流媒体状态
                try:
                    response = requests.get('http://localhost:7777/api/streamer/state')
                    state = response.json()
                    
                    # 记录指标
                    self.metrics['cpu_usage'].append(cpu_percent)
                    self.metrics['memory_usage'].append(memory_percent)
                    self.metrics['fps'].append(state.get('fps', 0))
                    self.metrics['bitrate'].append(state.get('bitrate', 0))
                    
                    logging.info(f"CPU: {cpu_percent}%, Memory: {memory_percent}%, FPS: {state.get('fps', 0)}, Bitrate: {state.get('bitrate', 0)}kbps")
                    
                except requests.exceptions.RequestException as e:
                    logging.error(f"获取流媒体状态失败: {e}")
                
                time.sleep(1)
                
        except KeyboardInterrupt:
            self.end_test()

    def end_test(self):
        """结束测试并生成报告"""
        if self.process:
            self.process.terminate()
            self.process.wait()
        
        self.end_time = time.time()
        duration = self.end_time - self.start_time
        
        # 计算平均值
        avg_metrics = {
            'cpu_usage': sum(self.metrics['cpu_usage']) / len(self.metrics['cpu_usage']),
            'memory_usage': sum(self.metrics['memory_usage']) / len(self.metrics['memory_usage']),
            'fps': sum(self.metrics['fps']) / len(self.metrics['fps']),
            'bitrate': sum(self.metrics['bitrate']) / len(self.metrics['bitrate'])
        }
        
        # 生成报告
        report = {
            'test_duration': duration,
            'average_metrics': avg_metrics,
            'raw_metrics': self.metrics
        }
        
        # 保存报告
        with open(f'performance_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json', 'w') as f:
            json.dump(report, f, indent=4)
        
        logging.info("测试完成，报告已生成")

if __name__ == '__main__':
    test = StreamerPerformanceTest()
    test.start_test() 