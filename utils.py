import os


# Midi Related
#------------------------------------
def plotMIDI(file_name):
	"""
	Usage: plotMIDI('video_games/dw2.mid')
	"""
	midi_data = pretty_midi.PrettyMIDI(file_name)
	roll = midi_data.get_piano_roll()

	plt.matshow(roll[:,:2000], aspect='auto', origin='lower', cmap='magma')
	plt.show()


# File Manipulation Related
#------------------------------------
def makedir(outputFolder):
	if not os.path.exists(outputFolder):
		os.makedirs(outputFolder)

import h5py
def write2hdf5(filename, dict2store, compression="lzf"):
	"""
	Write items in a dictionary to an hdf5file
	@type   filename    :   String
	@param  filename    :   Filename of the hdf5 file to output to.
	@type   dict2store  :   Dict
	@param  dict2store  :   Dictionary of items to store. The value should be an array.

	Usage: write2hdf5('encoded_data.h5',{'data':os.listdir('the_session_cleaned_checked_encoded')})
	"""
	with h5py.File(filename,'w') as hf:
		for key,value in dict2store.iteritems():
			hf.create_dataset(key, data=value,compression=compression)


def hdf52dict(hdf5Filename):
	"""
	Loads an HDF5 file of a game and returns a dictionary of the contents
	@type   hdf5Filename:   String
	@param  hdf5Filename:   Filename of the hdf5 file.
	"""
	retDict = {}
	with h5py.File(hdf5Filename,'r') as hf:
		for key in hf.keys():
			retDict[key] = np.array(hf.get(key))

	return retDict

def abc2h5(folderName='the_session_cleaned_checked_encoded', outputFile='encoded_data.h5'):
	encodeDict = {}
	for filestr in os.listdir(folderName):
		encodeDict[filestr] = np.load(os.path.join(folderName,filestr))
	write2hdf5(outputFile,encodeDict)

def testTrainSplit(folderName, trainRatio):
	"""
	Usage: testTrainSplit('the_session_cleaned', 0.1)
	"""
	songlist = set()
	for filename in os.listdir(folderName):
		songlist.add(filename[:filename.find('_')])

	songlist = list(songlist)

	splitIndx = int(len(songlist)*trainRatio)
	testSongs = songlist[:splitIndx]
	trainSongs = songlist[splitIndx:]

	pickle.dump(testSongs, open('test_songs.p','wb'))
	pickle.dump(trainSongs, open('train_songs.p','wb'))

#------------------------------------

# .abc Related
#------------------------------------
def findNumMeasures(music):
	return music.replace('||','|').replace('|||','|').count('|')

def transposeABC(fromFile, toFile, shiftLvl):
	"""
	Transposes the .abc file in @fromFile by @shiftLvl and saves it to @toFile

	abc2abc.exe taken from http://ifdo.ca/~seymour/runabc/top.html
	"""

	cmd = 'abcmidi_win32\\abc2abc.exe "%s" -V 0 -t %d -b -r > "%s"' \
			%(fromFile,shiftLvl,toFile)

	os.system(cmd)

MODE_MAJ = 0
MODE_MIN = 1
MODE_MIX = 2
MODE_DOR = 3
MODE_PHR = 4
MODE_LYD = 5
MODE_LOC = 6
def keySigDecomposer(line):
	"""
	Decompose the key signature into two portions- key and mode

	Returns:
	key - number of flats, negative for sharps
	mode - as defined by MODE_ constants
	"""

	# first determine the mode
	mode = MODE_MAJ

	searchList = [('mix',MODE_MIX),('dor',MODE_DOR),('phr',MODE_PHR),('lyd',MODE_LYD),
				  ('loc',MODE_LOC),('maj',MODE_MAJ),('min',MODE_MIN),('m',MODE_MIN),
				  ('p',MODE_PHR)]

	lower = line.lower()
	for searchTup in searchList:
		if searchTup[0] in lower:
			mode = searchTup[1]
			line = line[:lower.rfind(searchTup[0])]
			break

	# then determine the key
	keys = ['B#','E#','A#','D#','G#','C#','F#','B','E','A','D','G','C',
			'F','Bb','Eb','Ab','Db','Gb','Cb','Fb']
	mode_modifier = {MODE_MAJ:-12, MODE_MIN:-9, MODE_MIX:-11, MODE_DOR:-10, 
					 MODE_PHR:-8, MODE_LYD:-13, MODE_LOC:-7}

	key = keys.index(line) + mode_modifier[mode]

	return str(key),str(mode)

def loadCleanABC(abcname):
	"""
	Loads a file in .abc format (cleaned), and returns the meta data and music contained
	in the file. 
	
	@meta - dictionary of metadata, key is the metadata type (ex. 'K')
	@music - string of the music
	"""
	meta = {}
	counter = 7
	with open(abcname,'r') as abcfile:
		for line in abcfile:
			# break down the key signature into # of sharps and flats
			# and mode				
			if counter>0:
				if line[0]=='K':
					try:
						meta['K_key'],meta['K_mode'] = keySigDecomposer(line[2:-1])
					except:
						print 'Key signature decomposition failed for file: ' + abcname
						exit(0)
				elif line[0]=='M':
					if 'C' in line[2:-1]:
						meta['M'] = '4/4'
					else:
						meta['M'] = line[2:-1]
				else:
					meta[line[0]] = line[2:-1]
				counter -= 1
			else:
				music = line[:-1]

	notes = [chr(i) for i in range(ord('a'),ord('g')+1)]
	notes += [c.upper() for c in notes]
	# add metadata that we manually create
	meta['len'] = findNumMeasures(music)
	countList = Counter(music)
	timeSigNumerator = int(meta['M'][:meta['M'].find('/')])
	meta['complexity'] = (sum(countList[c] for c in notes)*100)/(meta['len']*timeSigNumerator)

	return meta,music

import subprocess
def passesABC2ABC(fromFile):
	"""
	Returns true if the .abc file in @fromFile passes the abc2abc.exe check
	"""
	cmd = 'abcmidi_win32\\abc2abc.exe'
	cmdlist = [cmd, fromFile]
	proc = subprocess.Popen(cmdlist, stdout=subprocess.PIPE, shell=True)

	(out, err) = proc.communicate()

	# error check
	errorCnt_bar = out.count('Error : Bar')
	errorCnt = out.count('Error')
	if errorCnt>2 or errorCnt!=errorCnt_bar:
		return False
	elif errorCnt>0:
		barErrorList = re.findall('Bar [0-9]+', out)
		for i,barStr in enumerate(barErrorList):
			barErrorList[i] = int(re.search('[0-9]+',barStr).group(0))

		if barErrorList[0] == 1:
			errorCnt -= 1

		if abs(findNumMeasures(out)-barErrorList[-1])<3:
			errorCnt -= 1

	return errorCnt==0