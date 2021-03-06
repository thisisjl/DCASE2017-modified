# -*- coding: utf-8 -*-
'''MusicTaggerCRNN model for Keras.

Code by github.com/keunwoochoi.

# Reference:

- [Music-auto_tagging-keras](https://github.com/keunwoochoi/music-auto_tagging-keras)
- [Keras code and weights files for popular deep learning models.](https://github.com/fchollet/deep-learning-models)

'''
from __future__ import print_function
from __future__ import absolute_import
import os
import sys
sys.path.append(os.path.split(os.path.dirname(os.path.realpath(__file__)))[0])
import pandas as pd
import numpy as np
from keras import backend as K
from keras.layers import Input, Dense
from keras.models import Model
from keras.layers import Dense, Dropout, Reshape, Permute
from keras.layers.convolutional import Conv2D
from keras.layers.convolutional import MaxPooling2D, ZeroPadding2D
from keras.layers.normalization import BatchNormalization
from keras.layers.advanced_activations import ELU
from keras.layers.recurrent import GRU
from keras.utils.data_utils import get_file
from keras.utils.layer_utils import convert_all_kernels_in_model
from keras.optimizers import Adam
# from audio_conv_utils import decode_predictions, preprocess_input
from dcase_framework.sketch_utils import get_callbacks, get_learner_params
from sketch_utils.audio_utils import VerySimpleGenerator

TH_WEIGHTS_PATH = 'https://github.com/fchollet/deep-learning-models/releases/download/v0.3/music_tagger_crnn_weights_tf_kernels_th_dim_ordering.h5'
TF_WEIGHTS_PATH = 'https://github.com/fchollet/deep-learning-models/releases/download/v0.3/music_tagger_crnn_weights_tf_kernels_tf_dim_ordering.h5'

TAGS = ['rock', 'pop', 'alternative', 'indie', 'electronic',
        'female vocalists', 'dance', '00s', 'alternative rock', 'jazz',
        'beautiful', 'metal', 'chillout', 'male vocalists',
        'classic rock', 'soul', 'indie rock', 'Mellow', 'electronica',
        '80s', 'folk', '90s', 'chill', 'instrumental', 'punk',
        'oldies', 'blues', 'hard rock', 'ambient', 'acoustic',
        'experimental', 'female vocalist', 'guitar', 'Hip-Hop',
        '70s', 'party', 'country', 'easy listening',
        'sexy', 'catchy', 'funk', 'electro', 'heavy metal',
        'Progressive rock', '60s', 'rnb', 'indie pop',
        'sad', 'House', 'happy']


def librosa_exists():
    try:
        __import__('librosa')
    except ImportError:
        return False
    else:
        return True


def preprocess_input(audio_path, dim_ordering='default'):
    '''Reads an audio file and outputs a Mel-spectrogram.
    '''
    if dim_ordering == 'default':
        dim_ordering = K.image_dim_ordering()
    assert dim_ordering in {'tf', 'th'}

    if librosa_exists():
        import librosa
    else:
        raise RuntimeError('Librosa is required to process audio files.\n' +
                           'Install it via `pip install librosa` \nor visit ' +
                           'http://librosa.github.io/librosa/ for details.')

    # mel-spectrogram parameters
    SR = 12000
    N_FFT = 512
    N_MELS = 96
    HOP_LEN = 256
    DURA = 29.12

    src, sr = librosa.load(audio_path, sr=SR)
    n_sample = src.shape[0]
    n_sample_wanted = int(DURA * SR)

    # trim the signal at the center
    if n_sample < n_sample_wanted:  # if too short
        src = np.hstack((src, np.zeros((int(DURA * SR) - n_sample,))))
    elif n_sample > n_sample_wanted:  # if too long
        src = src[int((n_sample - n_sample_wanted) / 2):
                  int((n_sample + n_sample_wanted) / 2)]

    logam = librosa.logamplitude
    melgram = librosa.feature.melspectrogram
    x = logam(melgram(y=src, sr=SR, hop_length=HOP_LEN,
                      n_fft=N_FFT, n_mels=N_MELS) ** 2,
              ref_power=1.0)

    if dim_ordering == 'th':
        x = np.expand_dims(x, axis=0)
    elif dim_ordering == 'tf':
        x = np.expand_dims(x, axis=3)
    return x


def decode_predictions(preds, top_n=5):
    '''Decode the output of a music tagger model.

    # Arguments
        preds: 2-dimensional numpy array
        top_n: integer in [0, 50], number of items to show

    '''
    assert len(preds.shape) == 2 and preds.shape[1] == 50
    results = []
    for pred in preds:
        result = zip(TAGS, pred)
        result = sorted(result, key=lambda x: x[1], reverse=True)
        results.append(result[:top_n])
    return results


def MusicTaggerCRNN(weights='msd', input_tensor=None, include_top=True):
    '''Instantiate the MusicTaggerCRNN architecture,
    optionally loading weights pre-trained
    on Million Song Dataset. Note that when using TensorFlow,
    for best performance you should set
    `image_dim_ordering="tf"` in your Keras config
    at ~/.keras/keras.json.

    The model and the weights are compatible with both
    TensorFlow and Theano. The dimension ordering
    convention used by the model is the one
    specified in your Keras config file.

    For preparing mel-spectrogram input, see
    `audio_conv_utils.py` in [applications](https://github.com/fchollet/keras/tree/master/keras/applications).
    You will need to install [Librosa](http://librosa.github.io/librosa/)
    to use it.

    # Arguments
        weights: one of `None` (random initialization)
            or "msd" (pre-training on ImageNet).
        input_tensor: optional Keras tensor (i.e. output of `layers.Input()`)
            to use as image input for the model.
        include_top: whether to include the 1 fully-connected
            layer (output layer) at the top of the network.
            If False, the network outputs 32-dim features.


    # Returns
        A Keras model instance.
    '''
    if weights not in {'msd', None}:
        raise ValueError('The `weights` argument should be either '
                         '`None` (random initialization) or `msd` '
                         '(pre-training on Million Song Dataset).')

    # Determine proper input shape
    if K.image_dim_ordering() == 'th':
        input_shape = (1, 96, 1366)
    else:
        input_shape = (96, 1366, 1)

    if input_tensor is None:
        melgram_input = Input(shape=input_shape)
    else:
        if not K.is_keras_tensor(input_tensor):
            melgram_input = Input(tensor=input_tensor, shape=input_shape)
        else:
            melgram_input = input_tensor

    # Determine input axis
    if K.image_dim_ordering() == 'th':
        channel_axis = 1
        freq_axis = 2
        time_axis = 3
    else:
        channel_axis = 3
        freq_axis = 1
        time_axis = 2

    # Input block
    x = ZeroPadding2D(padding=(0, 37))(melgram_input)
    x = BatchNormalization(axis=time_axis, name='bn_0_freq')(x)

    # Conv block 1
    x = Conv2D(64, kernel_size=(3, 3), padding='same', name='conv1')(x)
    x = BatchNormalization(axis=channel_axis, name='bn1')(x)
    x = ELU()(x)
    x = MaxPooling2D(pool_size=(2, 2), strides=(2, 2), name='pool1')(x)

    # Conv block 2
    x = Conv2D(128, kernel_size=(3, 3), padding='same', name='conv2')(x)
    x = BatchNormalization(axis=channel_axis, name='bn2')(x)
    x = ELU()(x)
    x = MaxPooling2D(pool_size=(3, 3), strides=(3, 3), name='pool2')(x)

    # Conv block 3
    x = Conv2D(128, kernel_size=(3, 3), padding='same', name='conv3')(x)
    x = BatchNormalization(axis=channel_axis, name='bn3')(x)
    x = ELU()(x)
    x = MaxPooling2D(pool_size=(4, 4), strides=(4, 4), name='pool3')(x)

    # Conv block 4
    x = Conv2D(128, kernel_size=(3, 3), padding='same', name='conv4')(x)
    x = BatchNormalization(axis=channel_axis, name='bn4')(x)
    x = ELU()(x)
    x = MaxPooling2D(pool_size=(4, 4), strides=(4, 4), name='pool4')(x)

    # reshaping
    if K.image_dim_ordering() == 'th':
        x = Permute((3, 1, 2))(x)
    x = Reshape((15, 128))(x)

    # GRU block 1, 2, output
    x = GRU(32, return_sequences=True, name='gru1')(x)
    x = GRU(32, return_sequences=False, name='gru2')(x)

    if include_top:
        x = Dense(50, activation='sigmoid', name='output')(x)

    # Create model
    model = Model(melgram_input, x)
    if weights is None:
        model.compile(optimizer=Adam(lr=5e-3), loss='binary_crossentropy', metrics=['categorical_accuracy'])
        return model
    else:
        # Load weights
        if K.image_dim_ordering() == 'tf':
            weights_path = get_file('music_tagger_crnn_weights_tf_kernels_tf_dim_ordering.h5',
                                    TF_WEIGHTS_PATH,
                                    cache_subdir='models')
        else:
            weights_path = get_file('music_tagger_crnn_weights_tf_kernels_th_dim_ordering.h5',
                                    TH_WEIGHTS_PATH,
                                    cache_subdir='models')
        model.load_weights(weights_path, by_name=True)
        if K.backend() == 'theano':
            convert_all_kernels_in_model(model)
        return model


def process_dataset_txt(txt_filename, dataset_path=None):
    def make_full_path(x): return os.path.join(dataset_path, 'audio', x)
    def make_list(x): return eval(x)
    df = pd.read_csv(os.path.join(dataset_path, txt_filename))
    df['path'] = df['path'].apply(make_full_path)
    df['label'] = df['label'].apply(make_list)
    return df


if __name__ == '__main__':
    model = MusicTaggerCRNN(weights=None)

    parameter_set = 'choi2016a'
    #dataset_path = '../../../Sound data bases/MagnatagatuneDataset/'
    dataset_path = '../../audio_databases/magnatagatune/'
    evaluation_setup = 'evaluation_setup'
    meta_file = 'index/meta.csv'
    fold = 1
    filename = './system/magnatagatune_sketch/{}'.format(parameter_set)  # TODO: create filename with hash
    train_folds = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    eval_folds = [12]
    test_folds = [13, 14, 15]

    params_filename = 'parameters.yaml'

    learner_params = get_learner_params(params_filename, parameter_set)
    callbacks = get_callbacks(filename, learner_params)

    # read and process meta, train, eval files - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    columns = ['path', 'scene_label', 'code']
    meta_df = process_dataset_txt(meta_file, dataset_path=dataset_path)

    train_df = meta_df[meta_df['fold'].isin(train_folds)]
    eval_df = meta_df[meta_df['fold'].isin(test_folds)]

    # create post processing list
    post_processing = [{'mel_spectrogram':{'enable': True}}]

    # create training and evaluation generators  - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
    train_vsg = VerySimpleGenerator(train_df,
                                    batch_size=10,
                                    mono=True,
                                    desired_fs=12000,
                                    label_str='label',
                                    post_processing_list=post_processing)

    # next(train_vsg.flow())

    eval_vsg = VerySimpleGenerator(eval_df,
                                   batch_size=10,
                                   mono=True,
                                   desired_fs=12000,
                                   label_str='label',
                                   post_processing_list=post_processing)

    model.fit_generator(train_vsg.flow(),
                        train_vsg.get_num_batches(),
                        verbose=1,
                        epochs=200,
                        callbacks=callbacks,
                        validation_data=eval_vsg.flow(),
                        validation_steps=eval_vsg.get_num_batches())

    # audio_path = 'audio_file.mp3'
    # melgram = preprocess_input(audio_path)
    # melgrams = np.expand_dims(melgram, axis=0)

    # preds = model.predict(melgrams)
    # print('Predicted:')
    # print(decode_predictions(preds))