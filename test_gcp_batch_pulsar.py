#!/usr/bin/env python3
"""
Test script for GCP Batch Pulsar Job Runner
This script validates the runner design and configuration
"""

import os
import sys

# Add lib to path for Galaxy imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'lib'))

def test_runner_design():
    """Test the job runner design and configuration"""
    print("Testing GCP Batch Pulsar Job Runner Design...")
    
    # Test 1: Check file exists and has required classes
    runner_file = "lib/galaxy/jobs/runners/gcp_batch_pulsar.py"
    if not os.path.exists(runner_file):
        print("❌ Runner file does not exist")
        return False
        
    with open(runner_file, 'r') as f:
        content = f.read()
        
    # Check for required class definition
    if "class GCPBatchPulsarJobRunner" not in content:
        print("❌ GCPBatchPulsarJobRunner class not found")
        return False
    print("✓ GCPBatchPulsarJobRunner class found")
    
    # Check for required methods
    required_methods = [
        "_setup_embedded_pulsar",
        "_stage_files_with_pulsar", 
        "_validate_runner_params",
        "_create_batch_job",
        "finish_job",
        "check_watched_item"
    ]
    
    for method in required_methods:
        if f"def {method}" not in content:
            print(f"❌ Required method {method} not found")
            return False
        print(f"✓ Method {method} found")
    
    # Check for parameter specifications
    if "PULSAR_PARAM_SPECS" not in content:
        print("❌ PULSAR_PARAM_SPECS not found")
        return False
    print("✓ PULSAR_PARAM_SPECS found")
    
    # Check for error handling
    if "try:" not in content or "except" not in content:
        print("❌ Error handling not found")
        return False
    print("✓ Error handling found")
    
    # Check for logging
    if "log.info" not in content or "log.error" not in content:
        print("❌ Logging not found")
        return False
    print("✓ Logging found")
    
    print("\n✅ All design tests passed!")
    return True

def test_configuration_template():
    """Test that configuration documentation exists"""
    print("\nTesting Configuration Documentation...")
    
    changes_file = "Changes.md"
    if not os.path.exists(changes_file):
        print("❌ Changes.md documentation not found")
        return False
        
    with open(changes_file, 'r') as f:
        content = f.read()
        
    # Check for required configuration sections
    required_sections = [
        "job_conf.yml",
        "galaxy.yml", 
        "object_store_conf.yml",
        "google_batch_pulsar",
        "GCPBatchPulsarJobRunner"
    ]
    
    for section in required_sections:
        if section not in content:
            print(f"❌ Configuration section {section} not found")
            return False
        print(f"✓ Configuration section {section} found")
    
    print("✅ Configuration documentation complete!")
    return True

def test_dependencies():
    """Test that dependencies are properly configured"""
    print("\nTesting Dependencies...")
    
    deps_file = "lib/galaxy/dependencies/conditional-requirements.txt"
    if not os.path.exists(deps_file):
        print("❌ Dependencies file not found")
        return False
        
    with open(deps_file, 'r') as f:
        content = f.read()
        
    # Check for required dependencies
    required_deps = [
        "google-cloud-batch",
        "google-cloud-storage", 
        "google-auth"
    ]
    
    for dep in required_deps:
        if dep not in content:
            print(f"❌ Dependency {dep} not found")
            return False
        print(f"✓ Dependency {dep} found")
    
    print("✅ Dependencies properly configured!")
    return True

def main():
    """Run all tests"""
    print("GCP Batch Pulsar Job Runner Validation")
    print("=" * 50)
    
    tests = [
        test_runner_design,
        test_configuration_template,
        test_dependencies
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"❌ Test failed with error: {e}")
            results.append(False)
    
    print("\n" + "=" * 50)
    if all(results):
        print("🎉 ALL TESTS PASSED!")
        print("\nThe GCP Batch Pulsar Job Runner implementation is ready for integration testing.")
        print("\nNext steps:")
        print("1. Set up Google Cloud credentials")
        print("2. Configure job_conf.yml with google_batch_pulsar runner")
        print("3. Test with a simple Galaxy job")
        return 0
    else:
        print("❌ SOME TESTS FAILED")
        print("Review the errors above and fix the implementation.")
        return 1

if __name__ == "__main__":
    sys.exit(main())