import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import tensorflow as tf
import numpy as np
import os
import sys
from models import CharRNN, Config, Seq2SeqRNN, CBOW, GenAdversarialNet
import pickle
import reader
import random
import utils_runtime
import utils_hyperparam
import utils
import re
import copy

tf_ver = tf.__version__
SHERLOCK = (str(tf_ver) == '0.12.1')

# TRAIN_DATA = '/data/small_processed/nn_input_train'
# DEVELOPMENT_DATA = '/data/small_processed/nn_input_test'

# for Sherlock
if SHERLOCK:
    DIR_MODIFIER = '/scratch/users/nipuna1'
    from tensorflow.contrib.metrics import confusion_matrix as tf_confusion_matrix
# for Azure
else:
    DIR_MODIFIER = '/data'

TRAIN_DATA = DIR_MODIFIER + '/full_dataset/char_rnn_dataset/nn_input_train_stride_25_window_25_nnType_char_rnn_shuffled'
TEST_DATA = DIR_MODIFIER + '/full_dataset/char_rnn_dataset/nn_input_test_stride_25_window_25_nnType_char_rnn_shuffled'
DEVELOPMENT_DATA = DIR_MODIFIER + '/full_dataset/char_rnn_dataset/nn_input_dev_stride_25_window_25_nnType_char_rnn_shuffled'

GAN_TRAIN_DATA = DIR_MODIFIER + '/full_dataset/gan_dataset/nn_input_train_stride_25_window_25_nnType_seq2seq_output_sz_25_shuffled'
GAN_TEST_DATA = DIR_MODIFIER + '/full_dataset/gan_dataset/nn_input_test_stride_25_window_25_nnType_seq2seq_output_sz_25_shuffled'
GAN_DEVELOPMENT_DATA = DIR_MODIFIER + '/full_dataset/gan_dataset/nn_input_dev_stride_25_window_25_nnType_seq2seq_output_sz_25_shuffled'

SUMMARY_DIR = DIR_MODIFIER + '/dev_summary2'

BATCH_SIZE = 100 # should be dynamically passed into Config
NUM_EPOCHS = 50
GPU_CONFIG = tf.ConfigProto()
GPU_CONFIG.gpu_options.per_process_gpu_memory_fraction = 0.3

# For T --> inf, p is uniform. Easy to sample from!
# For T --> 0, p "concentrates" on arg max. Hard to sample from!
TEMPERATURE = 1.0

meta_map = pickle.load(open(os.path.join(DIR_MODIFIER, 'full_dataset/global_map_meta.p'),'rb'))
music_map = pickle.load(open(os.path.join(DIR_MODIFIER, 'full_dataset/global_map_music.p'),'rb'))

def plot_confusion(confusion_matrix, vocabulary, epoch, characters_remove=[], annotate=False):
    # Get vocabulary components
    vocabulary_keys = music_map.keys()
    vocabulary_values = music_map.values()
    # print vocabulary_keys
    vocabulary_values, vocabulary_keys =  tuple([list(tup) for tup in zip(*sorted(zip(vocabulary_values, vocabulary_keys)))])
    # print vocabulary_keys

    removed_indicies = []
    for c in characters_remove:
        i = vocabulary_keys.index(c)
        vocabulary_keys.remove(c)
        index = vocabulary_values.pop(i)
        removed_indicies.append(index)

    # Delete unnecessary rows
    conf_temp = np.delete(confusion_matrix, removed_indicies, axis=0)
    # Delete unnecessary cols
    new_confusion = np.delete(conf_temp, removed_indicies, axis=1)


    vocabulary_values = range(len(vocabulary_keys))
    vocabulary_size = len(vocabulary_keys)

    fig, ax = plt.subplots(figsize=(10, 10))
    res = ax.imshow(new_confusion.astype(int), interpolation='nearest', cmap=plt.cm.jet)
    cb = fig.colorbar(res)

    if annotate:
        for x in xrange(vocabulary_size):
            for y in xrange(vocabulary_size):
                ax.annotate(str(new_confusion[x, y]), xy=(y, x),
                            horizontalalignment='center',
                            verticalalignment='center',
                            fontsize=4)

    plt.xticks(vocabulary_values, vocabulary_keys, fontsize=6)
    plt.yticks(vocabulary_values, vocabulary_keys, fontsize=6)
    fig.savefig('confusion_matrix_epoch{0}.png'.format(epoch))



def sample_Seq2Seq(args, curModel, cell_type, session, warm_chars, vocabulary, meta, batch_size):
    num_encode = [len(warm_chars)]
    num_decode = [1000]

    if cell_type == 'lstm':
        initial_state_sample = [[np.zeros(curModel.config.hidden_size) for entry in xrange(batch_size)] for layer in xrange(curModel.config.num_layers)]
    else:
        initial_state_sample = [np.zeros(curModel.config.hidden_size) for entry in xrange(batch_size)]

    feed_values = utils_runtime.pack_feed_values(args, [warm_chars],
                                [[vocabulary["<go>"]]], [np.zeros_like(meta)],
                                initial_state_sample, True,
                                num_encode, num_decode)
    # logits, state = curModel.sample(session, feed_values)
    prediction = curModel.sample(session, feed_values)
    print len(prediction[0])
    return prediction


def sampleCBOW(session, args, curModel, vocabulary_decode):
    # Sample Model
    warm_length = curModel.input_size
    warm_meta, warm_chars = utils_runtime.genWarmStartDataset(warm_length, meta_map, music_map)

    warm_meta_array = [warm_meta]
    # warm_meta_array = [warm_meta[:] for idx in xrange(3)]
    # warm_meta_array[1][4] = 1 - warm_meta_array[1][4]
    # warm_meta_array[1][3] = np.random.choice(11)

    print "Sampling from single RNN cell using warm start of ({0})".format(warm_length)
    for meta in warm_meta_array:
        print "Current Metadata: {0}".format(meta)
        generated = warm_chars[:]
        context_window = warm_chars[:]

        # Warm Start (get the first prediction)
        feed_values = utils_runtime.pack_feed_values(args, [context_window], [[0]*len(context_window)],
                                       None, None, None, None, None)
        logits,_ = curModel.sample(session, feed_values)

        # Sample
        sampled_character = utils_runtime.sample_with_temperature(logits, TEMPERATURE)
        #while sampled_character!=END_TOKEN_ID and len(generated) < 200:
        while len(generated) < 200:
            # update the context input for the model
            context_window = context_window[1:] + [sampled_character]

            feed_values = utils_runtime.pack_feed_values(args, [context_window], [[0]*len(context_window)],
                                           None, None, None, None, None)
            logits,_ = curModel.sample(session, feed_values)

            sampled_character = utils_runtime.sample_with_temperature(logits, TEMPERATURE)
            generated.append(sampled_character)

        decoded_characters = [vocabulary_decode[char] for char in generated]

        # Currently chopping off the last char regardless if its <end> or not
        encoding = utils.encoding2ABC(meta, generated[1:-1], meta_map, music_map)

    return encoding


def run_model(args):
    # used by song_generator.py
    if hasattr(args, 'temperature'):
        global TEMPERATURE
        TEMPERATURE = args.temperature

    if hasattr(args, 'warm_len'):
        warm_length = args.warm_len
    else:
        warm_length = 15

    if hasattr(args, 'meta_map'):
        global meta_map,music_map
        meta_map = pickle.load(open(os.path.join(DIR_MODIFIER, args.meta_map),'rb'))
        music_map = pickle.load(open(os.path.join(DIR_MODIFIER, args.music_map),'rb'))

    use_seq2seq_data = (args.model == 'seq2seq')
    if args.data_dir != '':
        dataset_dir = args.data_dir
    elif args.train == 'train':
        dataset_dir = GAN_TRAIN_DATA if use_seq2seq_data else TRAIN_DATA
    elif args.train == 'test':
        dataset_dir = GAN_TEST_DATA if use_seq2seq_data else TEST_DATA
    else: # args.train == 'dev' or 'sample' (which has no dataset, but we just read anyway)
        dataset_dir = GAN_DEVELOPMENT_DATA if use_seq2seq_data else DEVELOPMENT_DATA

    print 'Using dataset %s' %dataset_dir
    dateset_filenames = reader.abc_filenames(dataset_dir)

    # figure out the input data size
    window_sz = int(re.findall('[0-9]+', re.findall('window_[0-9]+', dataset_dir)[0])[0])
    if 'output_sz' in dataset_dir:
        label_sz = int(re.findall('[0-9]+', re.findall('output_sz_[0-9]+', dataset_dir)[0])[0])
    else:
        label_sz = window_sz

    input_size = 1 if (args.train == "sample" and args.model!='cbow') else window_sz
    initial_size = 7
    label_size = 1 if args.train == "sample" else label_sz
    batch_size = 1 if args.train == "sample" else BATCH_SIZE
    NUM_EPOCHS = args.num_epochs
    print "Using checkpoint directory: {0}".format(args.ckpt_dir)

    # Getting vocabulary mapping:
    vocab_sz = len(music_map)
    music_map["<start>"] = vocab_sz
    music_map["<end>"] = vocab_sz+1
    if use_seq2seq_data:
        music_map["<go>"] = vocab_sz+2

    vocabulary_size = len(music_map)
    vocabulary_decode = dict(zip(music_map.values(), music_map.keys()))

    start_encode = music_map["<go>"] if (args.train == "sample" and use_seq2seq_data) else music_map["<start>"]
    end_encode = music_map["<end>"]

    cell_type = 'lstm'
    # cell_type = 'gru'
    # cell_type = 'rnn'

    if args.model == 'seq2seq':
        curModel = Seq2SeqRNN(input_size, label_size, batch_size, vocabulary_size, cell_type, args.set_config, start_encode, end_encode)
        curModel.create_model(is_train = (args.train=='train'))
        curModel.train()
        curModel.metrics()

    elif args.model == 'char':
        curModel = CharRNN(input_size, label_size, batch_size, vocabulary_size, cell_type, args.set_config)
        curModel.create_model(is_train = (args.train=='train'))
        curModel.train()
        curModel.metrics()

    elif args.model == 'cbow':
        curModel = CBOW(input_size, batch_size, vocabulary_size, args.set_config)
        curModel.create_model()
        curModel.train()
        curModel.metrics()

    print "Running {0} model for {1} epochs.".format(args.model, NUM_EPOCHS)

    print "Reading in {0}-set filenames.".format(args.train)

    global_step = tf.Variable(0, trainable=False, name='global_step') #tf.contrib.framework.get_or_create_global_step()
    saver = tf.train.Saver(max_to_keep=NUM_EPOCHS)
    step = 0

    with tf.Session(config=GPU_CONFIG) as session:
        print "Inititialized TF Session!"

        # Checkpoint
        i_stopped, found_ckpt = utils_runtime.get_checkpoint(args, session, saver)

        # file_writer = tf.summary.FileWriter(SUMMARY_DIR, graph=session.graph, max_queue=10, flush_secs=30)
        file_writer = tf.summary.FileWriter(args.ckpt_dir, graph=session.graph, max_queue=10, flush_secs=30)
        confusion_matrix = np.zeros((vocabulary_size, vocabulary_size))
        batch_accuracies = []

        if args.train == "train":
            init_op = tf.global_variables_initializer() # tf.group(tf.initialize_all_variables(), tf.initialize_local_variables())
            init_op.run()
        else:
            # Exit if no checkpoint to test
            if not found_ckpt:
                return
            NUM_EPOCHS = i_stopped + 1

        # Sample Model
        if args.train == "sample":
            if args.model=='cbow':
                encoding = sampleCBOW(session, args, curModel, vocabulary_decode)
                return encoding

            # Sample Model
            if hasattr(args, 'warmupData'):
                warm_meta, warm_chars = utils_runtime.genWarmStartDataset(warm_length, meta_map, 
                                                          music_map, dataFolder=args.warmupData)
            else:
                warm_meta, warm_chars = utils_runtime.genWarmStartDataset(warm_length, meta_map, music_map)

            # warm_meta_array = [warm_meta[:] for idx in xrange(5)]
            warm_meta_array = [warm_meta[:] for idx in xrange(10)]

            # Change Key
            warm_meta_array[1][4] = 1 - warm_meta_array[1][4]
            # Change Number of Flats/Sharps
            warm_meta_array[2][3] = np.random.choice(11)
            # Lower Complexity
            warm_meta_array[3][6] = 50
            # Higher Complexity
            warm_meta_array[4][6] = 350
            # Higher LEngth
            warm_meta_array[5][5] = 30

            new_warm_meta = utils_runtime.encode_meta_batch(meta_map, warm_meta_array)
            new_warm_meta_array = zip(warm_meta_array, new_warm_meta)

            print "Sampling from single RNN cell using warm start of ({0})".format(warm_length)
            for old_meta, meta in new_warm_meta_array:
                print "Current Metadata: {0}".format(meta)
                generated = warm_chars[:]

                if args.model == 'char':
                    # Warm Start
                    for j, c in enumerate(warm_chars):
                        if cell_type == 'lstm':
                            if j == 0:
                                initial_state_sample = [[np.zeros(curModel.config.hidden_size) for entry in xrange(batch_size)] for layer in xrange(curModel.config.num_layers)]
                            else:
                                initial_state_sample = []
                                for lstm_tuple in state:
                                    initial_state_sample.append(lstm_tuple[0])
                        else:
                            initial_state_sample = [np.zeros(curModel.config.hidden_size) for entry in xrange(batch_size)] if (j == 0) else state[0]

                        feed_values = utils_runtime.pack_feed_values(args, [[c]],
                                                    [[0]], [meta],
                                                    initial_state_sample, (j == 0),
                                                    None, None)
                        logits, state = curModel.sample(session, feed_values)

                    # Sample
                    sampled_character = utils_runtime.sample_with_temperature(logits, TEMPERATURE)
                    while sampled_character != music_map["<end>"] and len(generated) < 100:
                        if cell_type == 'lstm':
                            initial_state_sample = []
                            for lstm_tuple in state:
                                initial_state_sample.append(lstm_tuple[0])
                        else:
                            initial_state_sample = state[0]

                        feed_values = utils_runtime.pack_feed_values(args, [[sampled_character]],
                                                    [[0]], [np.zeros_like(meta)],
                                                    initial_state_sample, False,
                                                    None, None)
                        logits, state = curModel.sample(session, feed_values)

                        sampled_character = utils_runtime.sample_with_temperature(logits, TEMPERATURE)
                        generated.append(sampled_character)

                elif args.model == 'seq2seq':
                    prediction = sample_Seq2Seq(args, curModel, cell_type, session, warm_chars, music_map, meta, batch_size)
                    generated.extend(prediction.flatten())

                decoded_characters = [vocabulary_decode[char] for char in generated]

                encoding = utils.encoding2ABC(old_meta, generated, meta_map, music_map)

                if hasattr(args, 'ran_from_script'):
                    return encoding

        # Train, dev, test model
        else:
            for i in xrange(i_stopped, NUM_EPOCHS):
                print "Running epoch ({0})...".format(i)
                random.shuffle(dateset_filenames)
                for j, data_file in enumerate(dateset_filenames):
                    # Get train data - into feed_dict
                    data = reader.read_abc_pickle(data_file)
                    random.shuffle(data)
                    data_batches = reader.abc_batch(data, n=batch_size)
                    for k, data_batch in enumerate(data_batches):
                        meta_batch, input_window_batch, output_window_batch = tuple([list(tup) for tup in zip(*data_batch)])
                        new_meta_batch = utils_runtime.encode_meta_batch(meta_map, meta_batch)

                        initial_state_batch = [[np.zeros(curModel.config.hidden_size) for entry in xrange(batch_size)] for layer in xrange(curModel.config.num_layers)]
                        num_encode = [window_sz] * batch_size
                        num_decode = num_encode[:]

                        feed_values = utils_runtime.pack_feed_values(args, input_window_batch,
                                                    output_window_batch, new_meta_batch,
                                                    initial_state_batch, True,
                                                    num_encode, num_decode)

                        summary, conf, accuracy = curModel.run(args, session, feed_values)

                        file_writer.add_summary(summary, step)

                        # Update confusion matrix
                        confusion_matrix += conf

                        # Record batch accuracies for test code
                        if args.train == "test" or args.train == 'dev':
                            batch_accuracies.append(accuracy)

                        # Processed another batch
                        step += 1

                if args.train == "train":
                    # Checkpoint model - every epoch
                    utils_runtime.save_checkpoint(args, session, saver, i)
                    confusion_suffix = str(i)
                else: # dev or test (NOT sample)
                    test_accuracy = np.mean(batch_accuracies)
                    print "Model {0} accuracy: {1}".format(args.train, test_accuracy)
                    confusion_suffix = "_{0}-set".format(args.train)

                    if args.train == 'dev':
                        # Update the file for choosing best hyperparameters
                        curFile = open(curModel.config.dev_filename, 'a')
                        curFile.write("Dev set accuracy: {0}".format(test_accuracy))
                        curFile.write('\n')
                        curFile.close()

                # Plot Confusion Matrix
                plot_confusion(confusion_matrix, music_map, confusion_suffix+"_all")
                plot_confusion(confusion_matrix, music_map, confusion_suffix+"_removed", characters_remove=['|', '2', '<end>'])

def main(_):

    args = utils_runtime.parseCommandLine()
    run_model(args)

    if args.train != "sample":
        if tf.gfile.Exists(SUMMARY_DIR):
            tf.gfile.DeleteRecursively(SUMMARY_DIR)
        tf.gfile.MakeDirs(SUMMARY_DIR)


if __name__ == "__main__":
    tf.app.run()
