#!/usr/bin/env python3
import subprocess
import time
import json
import os
from datetime import datetime
import logging
import shutil

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('performance_test_run.log'),
        logging.StreamHandler()
    ]
)

class PerformanceTestRunner:
    def __init__(self):
        self.results_dir = f"performance_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.baseline_dir = os.path.join(self.results_dir, "baseline")
        self.modified_dir = os.path.join(self.results_dir, "modified")
        
    def setup_directories(self):
        """创建结果目录"""
        os.makedirs(self.baseline_dir, exist_ok=True)
        os.makedirs(self.modified_dir, exist_ok=True)
        
    def backup_config(self):
        """备份当前配置"""
        if os.path.exists("/etc/kvmd/main.yaml"):
            shutil.copy2("/etc/kvmd/main.yaml", "/etc/kvmd/main.yaml.bak")
            logging.info("已备份当前配置到 /etc/kvmd/main.yaml.bak")
            
    def restore_config(self):
        """恢复原始配置"""
        if os.path.exists("/etc/kvmd/main.yaml.bak"):
            shutil.copy2("/etc/kvmd/main.yaml.bak", "/etc/kvmd/main.yaml")
            logging.info("已恢复原始配置")
            
    def run_tests(self, config_path: str, results_dir: str):
        """运行性能测试"""
        # 运行流媒体性能测试
        logging.info(f"运行流媒体性能测试...")
        subprocess.run(['python3', 'test_streamer_performance.py'])
        
        # 移动生成的报告到结果目录
        for file in os.listdir('.'):
            if file.startswith('performance_report_') and file.endswith('.json'):
                shutil.move(file, os.path.join(results_dir, file))
            elif file.startswith('streamer_performance.log'):
                shutil.move(file, os.path.join(results_dir, file))
        
        # 运行网络性能测试
        logging.info(f"运行网络性能测试...")
        subprocess.run(['python3', 'test_network_performance.py'])
        
        # 移动生成的报告到结果目录
        for file in os.listdir('.'):
            if file.startswith('network_performance_report_') and file.endswith('.json'):
                shutil.move(file, os.path.join(results_dir, file))
            elif file.startswith('network_performance.log'):
                shutil.move(file, os.path.join(results_dir, file))
                
    def compare_results(self):
        """比较测试结果"""
        baseline_reports = [f for f in os.listdir(self.baseline_dir) if f.endswith('.json')]
        modified_reports = [f for f in os.listdir(self.modified_dir) if f.endswith('.json')]
        
        comparison = {
            'streamer_performance': {},
            'network_performance': {}
        }
        
        # 比较流媒体性能
        for base, mod in zip(
            [f for f in baseline_reports if f.startswith('performance_report_')],
            [f for f in modified_reports if f.startswith('performance_report_')]
        ):
            with open(os.path.join(self.baseline_dir, base)) as f:
                base_data = json.load(f)
            with open(os.path.join(self.modified_dir, mod)) as f:
                mod_data = json.load(f)
                
            comparison['streamer_performance'] = {
                'cpu_usage': {
                    'baseline': base_data['average_metrics']['cpu_usage'],
                    'modified': mod_data['average_metrics']['cpu_usage'],
                    'improvement': ((base_data['average_metrics']['cpu_usage'] - 
                                  mod_data['average_metrics']['cpu_usage']) / 
                                 base_data['average_metrics']['cpu_usage'] * 100)
                },
                'memory_usage': {
                    'baseline': base_data['average_metrics']['memory_usage'],
                    'modified': mod_data['average_metrics']['memory_usage'],
                    'improvement': ((base_data['average_metrics']['memory_usage'] - 
                                  mod_data['average_metrics']['memory_usage']) / 
                                 base_data['average_metrics']['memory_usage'] * 100)
                },
                'fps': {
                    'baseline': base_data['average_metrics']['fps'],
                    'modified': mod_data['average_metrics']['fps'],
                    'improvement': ((mod_data['average_metrics']['fps'] - 
                                  base_data['average_metrics']['fps']) / 
                                 base_data['average_metrics']['fps'] * 100)
                }
            }
        
        # 比较网络性能
        for base, mod in zip(
            [f for f in baseline_reports if f.startswith('network_performance_report_')],
            [f for f in modified_reports if f.startswith('network_performance_report_')]
        ):
            with open(os.path.join(self.baseline_dir, base)) as f:
                base_data = json.load(f)
            with open(os.path.join(self.modified_dir, mod)) as f:
                mod_data = json.load(f)
                
            comparison['network_performance'] = {
                'latency': {
                    'baseline': base_data['statistics']['latency']['avg'],
                    'modified': mod_data['statistics']['latency']['avg'],
                    'improvement': ((base_data['statistics']['latency']['avg'] - 
                                  mod_data['statistics']['latency']['avg']) / 
                                 base_data['statistics']['latency']['avg'] * 100)
                },
                'bandwidth': {
                    'baseline': base_data['statistics']['bandwidth']['avg'],
                    'modified': mod_data['statistics']['bandwidth']['avg'],
                    'improvement': ((mod_data['statistics']['bandwidth']['avg'] - 
                                  base_data['statistics']['bandwidth']['avg']) / 
                                 base_data['statistics']['bandwidth']['avg'] * 100)
                }
            }
        
        # 保存比较结果
        with open(os.path.join(self.results_dir, 'comparison_report.json'), 'w') as f:
            json.dump(comparison, f, indent=4)
            
        # 打印比较结果
        logging.info("\n性能比较结果:")
        logging.info("\n流媒体性能:")
        logging.info(f"CPU使用率: {comparison['streamer_performance']['cpu_usage']['improvement']:.2f}% 改进")
        logging.info(f"内存使用率: {comparison['streamer_performance']['memory_usage']['improvement']:.2f}% 改进")
        logging.info(f"帧率: {comparison['streamer_performance']['fps']['improvement']:.2f}% 改进")
        
        logging.info("\n网络性能:")
        logging.info(f"延迟: {comparison['network_performance']['latency']['improvement']:.2f}% 改进")
        logging.info(f"带宽: {comparison['network_performance']['bandwidth']['improvement']:.2f}% 改进")
        
    def run(self, modified_config_path: str):
        """运行完整的测试流程"""
        try:
            self.setup_directories()
            self.backup_config()
            
            # 运行基准测试
            logging.info("运行基准测试...")
            self.run_tests("/etc/kvmd/main.yaml", self.baseline_dir)
            
            # 应用修改后的配置
            logging.info("应用修改后的配置...")
            shutil.copy2(modified_config_path, "/etc/kvmd/main.yaml")
            
            # 运行修改后的测试
            logging.info("运行修改后的测试...")
            self.run_tests(modified_config_path, self.modified_dir)
            
            # 比较结果
            self.compare_results()
            
        finally:
            # 恢复原始配置
            self.restore_config()
            
        logging.info(f"测试完成，结果保存在 {self.results_dir} 目录")

if __name__ == '__main__':
    import sys
    if len(sys.argv) != 2:
        print("Usage: python3 run_performance_tests.py <modified_config_path>")
        sys.exit(1)
        
    runner = PerformanceTestRunner()
    runner.run(sys.argv[1]) 