# Training / evaluation data goes here.
#
# CSV format — no header, one file per line:
#   /absolute/path/to/file.jpg,1    (1 = manipulated)
#   /absolute/path/to/file.wav,0    (0 = authentic)
#
# Recommended splits:
#   data/image_train.csv
#   data/image_val.csv
#   data/image_test.csv
#   data/video_train.csv
#   data/video_val.csv
#   data/video_test.csv
#   data/audio_train.csv
#   data/audio_val.csv
#   data/audio_test.csv
#
# Recommended datasets:
#   Image : FaceForensics++, DFDC, CIFAKE
#   Video : FaceForensics++, Celeb-DF
#   Audio : ASVspoof 2019/2021, WaveFake
