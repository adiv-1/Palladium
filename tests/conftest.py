import os
import tempfile
os.environ['DATA_DIR']=tempfile.mkdtemp(prefix='safesc-tests-')
os.environ['APP_TOKEN']='test-workspace-token'
os.environ['STREAMING_MODEL']='fixture'
os.environ['ENABLE_RELAY_VSR']='false'
os.environ['ENABLE_RELIFT']='false'
