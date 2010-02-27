#!/usr/bin/env python

#check for textboxes and other stuff:
#http://code.enthought.com/projects/traits/docs/html/tutorials/traits_ui_scientific_app.html

#matplotlib.use('gtkagg')

from lxml.etree import SubElement as Sub, parse, tostring
from lxml.etree import fromstring, Element
from optparse import OptionParser
import numpy as np
import sys
import subprocess
import httplib
import base64
import time
import urllib2
import warnings

from obspy.core import read, UTCDateTime
from obspy.seishub import Client
from obspy.signal.filter import bandpass, bandpassZPHSH, bandstop, bandstopZPHSH
from obspy.signal.filter import lowpass, lowpassZPHSH, highpass, highpassZPHSH
from obspy.signal.util import utlLonLat, utlGeoKm
from obspy.signal.invsim import estimateMagnitude
from obspy.imaging.spectrogram import spectrogram

import matplotlib
import matplotlib.pyplot as plt
from matplotlib.widgets import MultiCursor as mplMultiCursor
from matplotlib.widgets import Slider, Button, RadioButtons, CheckButtons
from matplotlib.patches import Ellipse

#imports for the buttons
import matplotlib.colors as colors
import matplotlib.patches as patches
import matplotlib.mathtext as mathtext
import matplotlib.artist as artist
import matplotlib.image as image

#Monkey patch (need to remember the ids of the mpl_connect-statements to remove them later)
#See source: http://matplotlib.sourcearchive.com/documentation/0.98.1/widgets_8py-source.html
class MultiCursor(mplMultiCursor):
    def __init__(self, canvas, axes, useblit=True, **lineprops):
        self.canvas = canvas
        self.axes = axes
        xmin, xmax = axes[-1].get_xlim()
        xmid = 0.5*(xmin+xmax)
        self.lines = [ax.axvline(xmid, visible=False, **lineprops) for ax in axes]
        self.visible = True
        self.useblit = useblit
        self.background = None
        self.needclear = False
        self.id1=self.canvas.mpl_connect('motion_notify_event', self.onmove)
        self.id2=self.canvas.mpl_connect('draw_event', self.clear)

#Some class definitions for the menu buttons
#code from: http://matplotlib.sourceforge.net/examples/widgets/menu.html
class ItemProperties:
    def __init__(self, fontsize=12, labelcolor='black', bgcolor='yellow', alpha=1.0):
        self.fontsize = fontsize
        self.labelcolor = labelcolor
        self.bgcolor = bgcolor
        self.alpha = alpha
        self.labelcolor_rgb = colors.colorConverter.to_rgb(labelcolor)
        self.bgcolor_rgb = colors.colorConverter.to_rgb(bgcolor)

class MenuItem(artist.Artist):
    parser = mathtext.MathTextParser("Bitmap")
    padx = 5
    pady = 5
    def __init__(self, fig, labelstr, props=None, hoverprops=None, on_select=None):
        artist.Artist.__init__(self)
        self.set_figure(fig)
        self.labelstr = labelstr
        if props is None:
            props = ItemProperties()
        if hoverprops is None:
            hoverprops = ItemProperties()
        self.props = props
        self.hoverprops = hoverprops
        self.on_select = on_select
        x, self.depth = self.parser.to_mask(
            labelstr, fontsize=props.fontsize, dpi=fig.dpi)
        if props.fontsize!=hoverprops.fontsize:
            raise NotImplementedError('support for different font sizes not implemented')
        self.labelwidth = x.shape[1]
        self.labelheight = x.shape[0]
        self.labelArray = np.zeros((x.shape[0], x.shape[1], 4))
        self.labelArray[:,:,-1] = x/255.
        self.label = image.FigureImage(fig, origin='upper')
        self.label.set_array(self.labelArray)
        # we'll update these later
        self.rect = patches.Rectangle((0,0), 1,1)
        self.set_hover_props(False)
        fig.canvas.mpl_connect('button_release_event', self.check_select)

    def check_select(self, event):
        over, junk = self.rect.contains(event)
        if not over:
            return
        if self.on_select is not None:
            self.on_select(self)

    def set_extent(self, x, y, w, h):
        #print x, y, w, h
        self.rect.set_x(x)
        self.rect.set_y(y)
        self.rect.set_width(w)
        self.rect.set_height(h)
        self.label.ox = x+self.padx
        self.label.oy = y-self.depth+self.pady/2.
        self.rect._update_patch_transform()
        self.hover = False

    def draw(self, renderer):
        self.rect.draw(renderer)
        self.label.draw(renderer)

    def set_hover_props(self, b):
        if b:
            props = self.hoverprops
        else:
            props = self.props
        r, g, b = props.labelcolor_rgb
        self.labelArray[:,:,0] = r
        self.labelArray[:,:,1] = g
        self.labelArray[:,:,2] = b
        self.label.set_array(self.labelArray)
        self.rect.set(facecolor=props.bgcolor, alpha=props.alpha)

    def set_hover(self, event):
        'check the hover status of event and return true if status is changed'
        b,junk = self.rect.contains(event)
        changed = (b != self.hover)
        if changed:
            self.set_hover_props(b)
        self.hover = b
        return changed

class Menu:
    def __init__(self, fig, menuitems):
        self.figure = fig
        fig.suppressComposite = True
        self.menuitems = menuitems
        self.numitems = len(menuitems)
        maxw = max([item.labelwidth for item in menuitems])
        maxh = max([item.labelheight for item in menuitems])
        totalh = self.numitems*maxh + (self.numitems+1)*2*MenuItem.pady
        x0 = 5
        y0 = 5
        y1 = y0 + (self.numitems-1)*(maxh + MenuItem.pady)
        width = maxw + 2*MenuItem.padx
        height = maxh+MenuItem.pady
        for item in menuitems:
            left = x0
            #bottom = y0-maxh-MenuItem.pady
            bottom = y1
            item.set_extent(left, bottom, width, height)
            fig.artists.append(item)
            y1 -= maxh + MenuItem.pady
        fig.canvas.mpl_connect('motion_notify_event', self.on_move)

    def on_move(self, event):
        draw = False
        for item in self.menuitems:
            draw = item.set_hover(event)
            if draw:
                self.figure.canvas.draw()
                break
    
def getCoord(network, station):
    """
    Returns longitude, latitude and elevation of given station
    """
    client = Client()
    coord = []

    resource = "dataless.seed.%s_%s.xml" % (network, station)
    xml = fromstring(client.station.getResource(resource, format='metadata'))

    for attrib in [u'Longitude (\xb0)', u'Latitude (\xb0)',  u'Elevation (m)']:
        node =  xml.xpath(u".//item[@title='%s']" % attrib)[0]
        value = float(node.getchildren()[0].attrib['text'])
        coord.append(value)

    return coord

class PickingGUI:

    def __init__(self, streams = None):
        self.streams = streams
        #Define some flags, dictionaries and plotting options
        self.flagFilt=False #False:no filter  True:filter
        self.flagFiltTyp=0 #0: bandpass 1: bandstop 2:lowpass 3: highpass
        self.dictFiltTyp={'Bandpass':0, 'Bandstop':1, 'Lowpass':2, 'Highpass':3}
        self.flagFiltZPH=False #False: no zero-phase True: zero-phase filtering
        self.valFiltLow=np.NaN # These are overridden with low/high estimated from sampling rate
        self.valFiltHigh=np.NaN
        self.flagWheelZoom=True #Switch use of mousewheel for zooming
        self.flagPhase=0 #0:P 1:S 2:Magnitude
        self.dictPhase={'P':0, 'S':1, 'Mag':2}
        self.dictPhaseInverse = {} # We need the reverted dictionary for switching throug the Phase radio button
        for i in self.dictPhase.items():
            self.dictPhaseInverse[i[1]] = i[0]
        self.dictPhaseColors={'P':'red', 'S':'blue', 'Psynth':'black', 'Ssynth':'black', 'Mag':'green'}
        self.dictPhaseLinestyles={'P':'-', 'S':'-', 'Psynth':'--', 'Ssynth':'--'}
        self.pickingColor = self.dictPhaseColors['P']
        self.magPickWindow=10 #Estimating the maximum/minimum in a sample-window around click
        self.magMinMarker='x'
        self.magMaxMarker='x'
        self.magMarkerEdgeWidth=1.8
        self.magMarkerSize=20
        self.axvlinewidths=1.2
        #dictionary for key-bindings
        self.dictKeybindings = {'setPick': 'alt', 'setPickError': ' ', 'delPick': 'escape',
                           'setMagMin': 'alt', 'setMagMax': ' ', 'switchPhase': 'control',
                           'delMagMinMax': 'escape', 'switchWheelZoom': 'z',
                           'switchPan': 'p', 'prevStream': 'y', 'nextStream': 'x',
                           'setPWeight0': '0', 'setPWeight1': '1', 'setPWeight2': '2',
                           'setPWeight3': '3', # 'setPWeight4': '4', 'setPWeight5': '5',
                           'setSWeight0': '0', 'setSWeight1': '1', 'setSWeight2': '2',
                           'setSWeight3': '3', # 'setSWeight4': '4', 'setSWeight5': '5',
                           'setPPolUp': 'u', 'setPPolPoorUp': '+',
                           'setPPolDown': 'd', 'setPPolPoorDown': '-',
                           'setSPolUp': 'u', 'setSPolPoorUp': '+',
                           'setSPolDown': 'd', 'setSPolPoorDown': '-',
                           'setPOnsetImpulsive': 'i', 'setPOnsetEmergent': 'e',
                           'setSOnsetImpulsive': 'i', 'setSOnsetEmergent': 'e'}
        self.threeDlocPath = '/baysoft/obspyck/3dloc/'
        self.threeDlocOutfile = self.threeDlocPath + '3dloc-out'
        self.threeDlocInfile = self.threeDlocPath + '3dloc-in'
        self.threeDlocPreCall = 'rm %s %s &> /dev/null' \
                % (self.threeDlocOutfile, self.threeDlocInfile)
        self.threeDlocCall = 'export D3_VELOCITY=/scratch/rh_vel/vp_5836/;' + \
                             'export D3_VELOCITY_2=/scratch/rh_vel/vs_32220/;' + \
                             'cd %s;' % self.threeDlocPath + \
                             '3dloc_pitsa'
        self.hyp2000Path = '/baysoft/obspyck/hyp_2000/'
        self.hyp2000Controlfile = self.hyp2000Path + 'bay2000.inp'
        self.hyp2000Phasefile = self.hyp2000Path + 'hyp2000.pha'
        self.hyp2000Stationsfile = self.hyp2000Path + 'stations.dat'
        self.hyp2000Summary = self.hyp2000Path + 'hypo.prt'
        self.hyp2000PreCall = 'rm %s %s %s &> /dev/null' \
                % (self.hyp2000Phasefile, self.hyp2000Stationsfile,
                   self.hyp2000Summary)
        self.hyp2000Call = 'export HYP2000_DATA=%s;' % (self.hyp2000Path) + \
                           'cd $HYP2000_DATA;' + \
                           'hyp2000 < bay2000.inp &> /dev/null'
        self.xmlEventID = None
        self.locationType = None
        self.flagSpectrogram = False
        # indicates which of the available events from seishub was loaded
        self.seishubEventCurrent = None 
        # indicates how many events are available from seishub
        self.seishubEventCount = None
        
        # Return, if no streams are given
        if not streams:
            return

        # Define some forbidden scenarios.
        # We assume there are:
        # - either one Z or three ZNE traces
        # - no two streams for any station
        self.stationlist=[]
        for st in streams:
            if not (len(st.traces) == 1 or len(st.traces) == 3):
                print 'Error: All streams must have either one Z trace or a set of three ZNE traces'
                return
            if len(st.traces) == 1 and st[0].stats.channel[-1] != 'Z':
                print 'Error: All streams must have either one Z trace or a set of three ZNE traces'
                return
            if len(st.traces) == 3 and (st[0].stats.channel[-1] != 'Z' or
                                        st[1].stats.channel[-1] != 'N' or
                                        st[2].stats.channel[-1] != 'E' or
                                        st[0].stats.station.strip() !=
                                        st[1].stats.station.strip() or
                                        st[0].stats.station.strip() !=
                                        st[2].stats.station.strip()):
                print 'Error: All streams must have either one Z trace or a set of ZNE traces (from the same station)'
                return
            self.stationlist.append(st[0].stats.station.strip())
        if len(self.stationlist) != len(set(self.stationlist)):
            print 'Error: Found two streams for one station'
            return

        #set up a list of dictionaries to store all picking data
        # set all station magnitude use-flags False
        self.dicts = []
        self.dictsMap = {} #XXX not used yet!
        self.eventMapColors = []
        client1 = Client()
        for i in range(len(self.streams)):
            self.dicts.append({})
            self.dicts[i]['MagUse'] = True
            station = streams[i][0].stats.station.strip()
            self.dicts[i]['Station'] = station
            self.dictsMap[station] = self.dicts[i]
            self.eventMapColors.append((0.,  1.,  0.,  1.))
            #XXX uncomment following lines for use with dynamically acquired data from seishub!
            net = streams[i][0].stats.network.strip()
            if net == '':
                net = 'BW'
                print "got no network information, setting to default: BW"
            sta = self.dicts[i]['Station']
            print sta
            date = streams[i][0].stats.starttime.date
            print 'fetching station data from seishub...'
            lon, lat, ele = getCoord(net, sta)
            print 'done.'
            self.dicts[i]['pazZ'] = client1.station.getPAZ(net, sta, date, channel_id = streams[i][0].stats.channel)
            self.dicts[i]['pazN'] = client1.station.getPAZ(net, sta, date, channel_id = streams[i][1].stats.channel)
            self.dicts[i]['pazE'] = client1.station.getPAZ(net, sta, date, channel_id = streams[i][2].stats.channel)
            self.dicts[i]['Station'] = self.stationlist[i]
            self.dicts[i]['StaLon'] = lon
            self.dicts[i]['StaLat'] = lat
            self.dicts[i]['StaEle'] = ele / 1000. # all depths in km!

        #XXX Remove lines for use with dynamically acquired data from seishub!
        #self.dicts[0]['StaLon'] = 12.795714
        #self.dicts[1]['StaLon'] = 12.864466
        #self.dicts[2]['StaLon'] = 12.867100
        #self.dicts[3]['StaLon'] = 12.824082
        #self.dicts[4]['StaLon'] = 12.729887
        #self.dicts[0]['StaLat'] = 47.737167
        #self.dicts[1]['StaLat'] = 47.761658
        #self.dicts[2]['StaLat'] = 47.740501
        #self.dicts[3]['StaLat'] = 47.745098
        #self.dicts[4]['StaLat'] = 47.744171
        #self.dicts[0]['StaEle'] = 0.860000
        #self.dicts[1]['StaEle'] = 0.815000
        #self.dicts[2]['StaEle'] = 0.555000
        #self.dicts[3]['StaEle'] = 1.162000
        #self.dicts[4]['StaEle'] = 0.763000
        #self.dicts[0]['pazZ'] = {'gain': 1.0,
        #                         'poles': [(-4.444+4.444j), (-4.444-4.444j), (-1.083+0j)],
        #                         'sensitivity': 671140000.0,
        #                         'zeros': [0j, 0j, 0j]}
        #self.dicts[0]['pazN'] = {'gain': 1.0,
        #                         'poles': [(-4.444+4.444j), (-4.444-4.444j), (-1.083+0j)],
        #                         'sensitivity': 671140000.0,
        #                         'zeros': [0j, 0j, 0j]}
        #self.dicts[0]['pazE'] = {'gain': 1.0,
        #                         'poles': [(-4.444+4.444j), (-4.444-4.444j), (-1.083+0j)],
        #                         'sensitivity': 671140000.0,
        #                         'zeros': [0j, 0j, 0j]}
        #self.dicts[1]['pazZ'] = {'gain': 1.0,
        #                         'poles': [(-4.444+4.444j), (-4.444-4.444j), (-1.083+0j)],
        #                         'sensitivity': 671140000.0,
        #                         'zeros': [0j, 0j, 0j]}
        #self.dicts[1]['pazN'] = {'gain': 1.0,
        #                         'poles': [(-4.444+4.444j), (-4.444-4.444j), (-1.083+0j)],
        #                         'sensitivity': 671140000.0,
        #                         'zeros': [0j, 0j, 0j]}
        #self.dicts[1]['pazE'] = {'gain': 1.0,
        #                         'poles': [(-4.444+4.444j), (-4.444-4.444j), (-1.083+0j)],
        #                         'sensitivity': 671140000.0,
        #                         'zeros': [0j, 0j, 0j]}
        #self.dicts[2]['pazZ'] = {'gain': 1.0,
        #                         'poles': [(-4.444+4.444j), (-4.444-4.444j), (-1.083+0j)],
        #                         'sensitivity': 671140000.0,
        #                         'zeros': [0j, 0j, 0j]}
        #self.dicts[2]['pazN'] = {'gain': 1.0,
        #                         'poles': [(-4.444+4.444j), (-4.444-4.444j), (-1.083+0j)],
        #                         'sensitivity': 671140000.0,
        #                         'zeros': [0j, 0j, 0j]}
        #self.dicts[2]['pazE'] = {'gain': 1.0,
        #                         'poles': [(-4.444+4.444j), (-4.444-4.444j), (-1.083+0j)],
        #                         'sensitivity': 671140000.0,
        #                         'zeros': [0j, 0j, 0j]}
        #self.dicts[3]['pazZ'] = {'gain': 1.0,
        #                         'poles': [(-4.444+4.444j), (-4.444-4.444j), (-1.083+0j)],
        #                         'sensitivity': 671140000.0,
        #                         'zeros': [0j, 0j, 0j]}
        #self.dicts[3]['pazN'] = {'gain': 1.0,
        #                         'poles': [(-4.444+4.444j), (-4.444-4.444j), (-1.083+0j)],
        #                         'sensitivity': 671140000.0,
        #                         'zeros': [0j, 0j, 0j]}
        #self.dicts[3]['pazE'] = {'gain': 1.0,
        #                         'poles': [(-4.444+4.444j), (-4.444-4.444j), (-1.083+0j)],
        #                         'sensitivity': 671140000.0,
        #                         'zeros': [0j, 0j, 0j]}
        #self.dicts[4]['pazZ'] = {'gain': 1.0,
        #                         'poles': [(-4.444+4.444j), (-4.444-4.444j), (-1.083+0j)],
        #                         'sensitivity': 671140000.0,
        #                         'zeros': [0j, 0j, 0j]}
        #self.dicts[4]['pazN'] = {'gain': 1.0,
        #                         'poles': [(-4.444+4.444j), (-4.444-4.444j), (-1.083+0j)],
        #                         'sensitivity': 671140000.0,
        #                         'zeros': [0j, 0j, 0j]}
        #self.dicts[4]['pazE'] = {'gain': 1.0,
        #                         'poles': [(-4.444+4.444j), (-4.444-4.444j), (-1.083+0j)],
        #                         'sensitivity': 671140000.0,
        #                         'zeros': [0j, 0j, 0j]}

        ##XXX only for testing purposes
        #self.dicts[0]['Mag'] = 1.34
        #self.dicts[1]['Mag'] = 1.03
        ##self.dicts[2]['Mag'] = 1.22
        #self.dicts[3]['Mag'] = 0.65
        #self.dicts[4]['Mag'] = 0.96

        #Define a pointer to navigate through the streams
        self.stNum=len(streams)
        self.stPt=0
    
        # Set up initial plot
        self.fig = plt.figure()
        self.fig.set_size_inches(20, 10, forward = True)
        self.drawAxes()
        self.addFiltButtons()
        self.addPhaseButtons()
        self.addSliders()
        #redraw()
        self.fig.canvas.draw()
        # Activate all mouse/key/Cursor-events
        self.keypress = self.fig.canvas.mpl_connect('key_press_event', self.pick)
        self.keypressWheelZoom = self.fig.canvas.mpl_connect('key_press_event', self.switchWheelZoom)
        self.keypressPan = self.fig.canvas.mpl_connect('key_press_event', self.switchPan)
        self.keypressNextPrev = self.fig.canvas.mpl_connect('key_press_event', self.switchStream)
        self.keypressSwitchPhase = self.fig.canvas.mpl_connect('key_press_event', self.switchPhase)
        self.buttonpressBlockRedraw = self.fig.canvas.mpl_connect('button_press_event', self.blockRedraw)
        self.buttonreleaseAllowRedraw = self.fig.canvas.mpl_connect('button_release_event', self.allowRedraw)
        self.scroll = self.fig.canvas.mpl_connect('scroll_event', self.zoom)
        self.scroll_button = self.fig.canvas.mpl_connect('button_press_event', self.zoom_reset)
        self.fig.canvas.toolbar.zoom()
        self.fig.canvas.widgetlock.release(self.fig.canvas.toolbar)
        #multicursor = mplMultiCursor(fig.canvas,axs, useblit=True, color='black', linewidth=1, ls='dotted')
        self.multicursor = MultiCursor(self.fig.canvas,self.axs, useblit=True, color=self.dictPhaseColors['P'], linewidth=1, ls='dotted')
        for l in self.multicursor.lines:
            l.set_color(self.dictPhaseColors['P'])
        self.radioPhase.circles[0].set_facecolor(self.dictPhaseColors['P'])
        #add menu buttons:
        props = ItemProperties(labelcolor='black', bgcolor='yellow', fontsize=12, alpha=0.2)
        hoverprops = ItemProperties(labelcolor='white', bgcolor='blue', fontsize=12, alpha=0.2)
        menuitems = []
        for label in ('clearAll', 'clearEvent', 'doHyp2000', 'do3dloc', 'calcMag', 'showMap', 'sendEvent', 'getNextEvent', 'quit'):
            def on_select(item):
                print '--> ', item.labelstr
                if item.labelstr == 'quit':
                    plt.close()
                elif item.labelstr == 'clearAll':
                    self.delAllItems()
                    self.clearDictionaries()
                    self.locationType = None
                    self.drawAllItems()
                    self.redraw()
                elif item.labelstr == 'clearEvent':
                    self.delAllItems()
                    self.clearEventDictionaries()
                    self.locationType = None
                    self.drawAllItems()
                    self.redraw()
                elif item.labelstr == 'doHyp2000':
                    self.delAllItems()
                    self.clearEventDictionaries()
                    self.locationType = "hyp2000"
                    self.doHyp2000()
                    #self.load3dlocSyntheticPhases()
                    self.loadHyp2000Data()
                    self.calculateEpiHypoDists()
                    self.calculateStationMagnitudes()
                    self.updateNetworkMag()
                    self.showEventMap()
                    self.drawAllItems()
                    self.redraw()
                elif item.labelstr == 'do3dloc':
                    self.delAllItems()
                    self.clearEventDictionaries()
                    self.locationType = "3dloc"
                    self.do3dLoc()
                    self.load3dlocSyntheticPhases()
                    self.load3dlocData()
                    self.calculateEpiHypoDists()
                    self.calculateStationMagnitudes()
                    self.updateNetworkMag()
                    self.showEventMap()
                    self.drawAllItems()
                    self.redraw()
                elif item.labelstr == 'calcMag':
                    self.calculateEpiHypoDists()
                    self.calculateStationMagnitudes()
                    self.updateNetworkMag()
                elif item.labelstr == 'showMap':
                    #self.load3dlocData()
                    self.showEventMap()
                elif item.labelstr == 'sendEvent':
                    self.uploadSeishub()
                elif item.labelstr == 'getNextEvent':
                    message = "Using start and endtime of first trace in " + \
                              "first stream to search for events."
                    warnings.warn(message)
                    self.delAllItems()
                    self.clearDictionaries()
                    self.getNextEventFromSeishub(self.streams[0][0].stats.starttime, 
                                             self.streams[0][0].stats.endtime)
                    print "Event data from seishub loaded."
                    self.drawAllItems()
                    self.redraw()
            item = MenuItem(self.fig, label, props=props, hoverprops=hoverprops, on_select=on_select)
            menuitems.append(item)
        self.menu = Menu(self.fig, menuitems)
        
        
        
        plt.show()
    
    
    def switch_flagFilt(self):
        self.flagFilt=not self.flagFilt
    def switch_flagFiltZPH(self):
        self.flagFiltZPH=not self.flagFiltZPH
    
    ## Trim all to same length, us Z as reference
    #start, end = stZ[0].stats.starttime, stZ[0].stats.endtime
    #stN.trim(start, end)
    #stE.trim(start, end)
    
    
    def drawAxes(self):
        self.t = np.arange(self.streams[self.stPt][0].stats.npts)
        self.axs = []
        self.plts = []
        self.trans = []
        trNum = len(self.streams[self.stPt].traces)
        for i in range(trNum):
            if i == 0:
                self.axs.append(self.fig.add_subplot(trNum,1,i+1))
                self.trans.append(matplotlib.transforms.blended_transform_factory(self.axs[i].transData,
                                                                             self.axs[i].transAxes))
            else:
                self.axs.append(self.fig.add_subplot(trNum,1,i+1,sharex=self.axs[0],sharey=self.axs[0]))
                self.trans.append(matplotlib.transforms.blended_transform_factory(self.axs[i].transData,
                                                                             self.axs[i].transAxes))
            self.axs[i].set_ylabel(self.streams[self.stPt][i].stats.station+" "+self.streams[self.stPt][i].stats.channel)
            if not self.flagSpectrogram:
                self.plts.append(self.axs[i].plot(self.t, self.streams[self.stPt][i].data, color='k',zorder=1000)[0])
            else:
                spectrogram(self.streams[self.stPt][i].data,
                            self.streams[self.stPt][i].stats.sampling_rate,
                            axis = self.axs[i],
                            nwin = self.streams[self.stPt][i].stats.npts * 4 / self.streams[self.stPt][i].stats.sampling_rate)
        self.supTit=self.fig.suptitle("%s -- %s, %s" % (self.streams[self.stPt][0].stats.starttime, self.streams[self.stPt][0].stats.endtime, self.streams[self.stPt][0].stats.station))
        self.xMin, self.xMax=self.axs[0].get_xlim()
        self.yMin, self.yMax=self.axs[0].get_ylim()
        self.fig.subplots_adjust(bottom=0.20,hspace=0,right=0.999,top=0.95)
    
    def drawSavedPicks(self):
        self.drawPLine()
        self.drawPLabel()
        self.drawPErr1Line()
        self.drawPErr2Line()
        self.drawPsynthLine()
        self.drawPsynthLabel()
        self.drawSLine()
        self.drawSLabel()
        self.drawSErr1Line()
        self.drawSErr2Line()
        self.drawSsynthLine()
        self.drawSsynthLabel()
        self.drawMagMinCross1()
        self.drawMagMaxCross1()
        self.drawMagMinCross2()
        self.drawMagMaxCross2()
    
    def drawPLine(self):
        if not self.dicts[self.stPt].has_key('P'):
            return
        self.PLines=[]
        for i in range(len(self.axs)):
            self.PLines.append(self.axs[i].axvline(self.dicts[self.stPt]['P'],color=self.dictPhaseColors['P'],linewidth=self.axvlinewidths,label='P',linestyle=self.dictPhaseLinestyles['P']))
    
    def delPLine(self):
        try:
            for i in range(len(self.axs)):
                self.axs[i].lines.remove(self.PLines[i])
        except:
            pass
        try:
            del self.PLines
        except:
            pass
    
    def drawPsynthLine(self):
        if not self.dicts[self.stPt].has_key('Psynth'):
            return
        self.PsynthLines=[]
        for i in range(len(self.axs)):
            self.PsynthLines.append(self.axs[i].axvline(self.dicts[self.stPt]['Psynth'],color=self.dictPhaseColors['Psynth'],linewidth=self.axvlinewidths,label='Psynth',linestyle=self.dictPhaseLinestyles['Psynth']))
    
    def delPsynthLine(self):
        try:
            for i in range(len(self.axs)):
                self.axs[i].lines.remove(self.PsynthLines[i])
        except:
            pass
        try:
            del self.PsynthLines
        except:
            pass
    
    def drawPLabel(self):
        if not self.dicts[self.stPt].has_key('P'):
            return
        PLabelString = 'P:'
        if not self.dicts[self.stPt].has_key('POnset'):
            PLabelString += '_'
        else:
            if self.dicts[self.stPt]['POnset'] == 'impulsive':
                PLabelString += 'I'
            elif self.dicts[self.stPt]['POnset'] == 'emergent':
                PLabelString += 'E'
            else:
                PLabelString += '?'
        if not self.dicts[self.stPt].has_key('PPol'):
            PLabelString += '_'
        else:
            if self.dicts[self.stPt]['PPol'] == 'up':
                PLabelString += 'U'
            elif self.dicts[self.stPt]['PPol'] == 'poorup':
                PLabelString += '+'
            elif self.dicts[self.stPt]['PPol'] == 'down':
                PLabelString += 'D'
            elif self.dicts[self.stPt]['PPol'] == 'poordown':
                PLabelString += '-'
            else:
                PLabelString += '?'
        if not self.dicts[self.stPt].has_key('PWeight'):
            PLabelString += '_'
        else:
            PLabelString += str(self.dicts[self.stPt]['PWeight'])
        self.PLabel = self.axs[0].text(self.dicts[self.stPt]['P'], 1 - 0.04 * len(self.axs),
                                       '  ' + PLabelString, transform = self.trans[0],
                                       color = self.dictPhaseColors['P'],
                                       family = 'monospace')
    
    def delPLabel(self):
        try:
            self.axs[0].texts.remove(self.PLabel)
        except:
            pass
        try:
            del self.PLabel
        except:
            pass
    
    def drawPsynthLabel(self):
        if not self.dicts[self.stPt].has_key('Psynth'):
            return
        PsynthLabelString = 'Psynth: %+.3fs' % self.dicts[self.stPt]['Pres']
        self.PsynthLabel = self.axs[0].text(self.dicts[self.stPt]['Psynth'], 1 - 0.08 * len(self.axs), '  ' + PsynthLabelString,
                             transform = self.trans[0], color=self.dictPhaseColors['Psynth'])
    
    def delPsynthLabel(self):
        try:
            self.axs[0].texts.remove(self.PsynthLabel)
        except:
            pass
        try:
            del self.PsynthLabel
        except:
            pass
    
    def drawPErr1Line(self):
        if not self.dicts[self.stPt].has_key('P') or not self.dicts[self.stPt].has_key('PErr1'):
            return
        self.PErr1Lines=[]
        for i in range(len(self.axs)):
            self.PErr1Lines.append(self.axs[i].axvline(self.dicts[self.stPt]['PErr1'],ymin=0.25,ymax=0.75,color=self.dictPhaseColors['P'],linewidth=self.axvlinewidths,label='PErr1'))
    
    def delPErr1Line(self):
        try:
            for i in range(len(self.axs)):
                self.axs[i].lines.remove(self.PErr1Lines[i])
        except:
            pass
        try:
            del self.PErr1Lines
        except:
            pass
    
    def drawPErr2Line(self):
        if not self.dicts[self.stPt].has_key('P') or not self.dicts[self.stPt].has_key('PErr2'):
            return
        self.PErr2Lines=[]
        for i in range(len(self.axs)):
            self.PErr2Lines.append(self.axs[i].axvline(self.dicts[self.stPt]['PErr2'],ymin=0.25,ymax=0.75,color=self.dictPhaseColors['P'],linewidth=self.axvlinewidths,label='PErr2'))
    
    def delPErr2Line(self):
        try:
            for i in range(len(self.axs)):
                self.axs[i].lines.remove(self.PErr2Lines[i])
        except:
            pass
        try:
            del self.PErr2Lines
        except:
            pass

    def drawSLine(self):
        if not self.dicts[self.stPt].has_key('S'):
            return
        self.SLines=[]
        for i in range(len(self.axs)):
            self.SLines.append(self.axs[i].axvline(self.dicts[self.stPt]['S'],color=self.dictPhaseColors['S'],linewidth=self.axvlinewidths,label='S',linestyle=self.dictPhaseLinestyles['S']))
    
    def delSLine(self):
        try:
            for i in range(len(self.axs)):
                self.axs[i].lines.remove(self.SLines[i])
        except:
            pass
        try:
            del self.SLines
        except:
            pass
    
    def drawSsynthLine(self):
        if not self.dicts[self.stPt].has_key('Ssynth'):
            return
        self.SsynthLines=[]
        for i in range(len(self.axs)):
            self.SsynthLines.append(self.axs[i].axvline(self.dicts[self.stPt]['Ssynth'],color=self.dictPhaseColors['Ssynth'],linewidth=self.axvlinewidths,label='Ssynth',linestyle=self.dictPhaseLinestyles['Ssynth']))
    
    def delSsynthLine(self):
        try:
            for i in range(len(self.axs)):
                self.axs[i].lines.remove(self.SsynthLines[i])
        except:
            pass
        try:
            del self.SsynthLines
        except:
            pass
    
    def drawSLabel(self):
        if not self.dicts[self.stPt].has_key('S'):
            return
        SLabelString = 'S:'
        if not self.dicts[self.stPt].has_key('SOnset'):
            SLabelString += '_'
        else:
            if self.dicts[self.stPt]['SOnset'] == 'impulsive':
                SLabelString += 'I'
            elif self.dicts[self.stPt]['SOnset'] == 'emergent':
                SLabelString += 'E'
            else:
                SLabelString += '?'
        if not self.dicts[self.stPt].has_key('SPol'):
            SLabelString += '_'
        else:
            if self.dicts[self.stPt]['SPol'] == 'up':
                SLabelString += 'U'
            elif self.dicts[self.stPt]['SPol'] == 'poorup':
                SLabelString += '+'
            elif self.dicts[self.stPt]['SPol'] == 'down':
                SLabelString += 'D'
            elif self.dicts[self.stPt]['SPol'] == 'poordown':
                SLabelString += '-'
            else:
                SLabelString += '?'
        if not self.dicts[self.stPt].has_key('SWeight'):
            SLabelString += '_'
        else:
            SLabelString += str(self.dicts[self.stPt]['SWeight'])
        self.SLabel = self.axs[0].text(self.dicts[self.stPt]['S'], 1 - 0.04 * len(self.axs),
                                       '  ' + SLabelString, transform = self.trans[0],
                                       color = self.dictPhaseColors['S'],
                                       family = 'monospace')
    
    def delSLabel(self):
        try:
            self.axs[0].texts.remove(self.SLabel)
        except:
            pass
        try:
            del self.SLabel
        except:
            pass
    
    def drawSsynthLabel(self):
        if not self.dicts[self.stPt].has_key('Ssynth'):
            return
        SsynthLabelString = 'Ssynth: %+.3fs' % self.dicts[self.stPt]['Sres']
        self.SsynthLabel = self.axs[0].text(self.dicts[self.stPt]['Ssynth'], 1 - 0.08 * len(self.axs), '\n  ' + SsynthLabelString,
                             transform = self.trans[0], color=self.dictPhaseColors['Ssynth'])
    
    def delSsynthLabel(self):
        try:
            self.axs[0].texts.remove(self.SsynthLabel)
        except:
            pass
        try:
            del self.SsynthLabel
        except:
            pass
    
    def drawSErr1Line(self):
        if not self.dicts[self.stPt].has_key('S') or not self.dicts[self.stPt].has_key('SErr1'):
            return
        self.SErr1Lines=[]
        for i in range(len(self.axs)):
            self.SErr1Lines.append(self.axs[i].axvline(self.dicts[self.stPt]['SErr1'],ymin=0.25,ymax=0.75,color=self.dictPhaseColors['S'],linewidth=self.axvlinewidths,label='SErr1'))
    
    def delSErr1Line(self):
        try:
            for i in range(len(self.axs)):
                self.axs[i].lines.remove(self.SErr1Lines[i])
        except:
            pass
        try:
            del self.SErr1Lines
        except:
            pass
    
    def drawSErr2Line(self):
        if not self.dicts[self.stPt].has_key('S') or not self.dicts[self.stPt].has_key('SErr2'):
            return
        self.SErr2Lines=[]
        for i in range(len(self.axs)):
            self.SErr2Lines.append(self.axs[i].axvline(self.dicts[self.stPt]['SErr2'],ymin=0.25,ymax=0.75,color=self.dictPhaseColors['S'],linewidth=self.axvlinewidths,label='SErr2'))
    
    def delSErr2Line(self):
        try:
            for i in range(len(self.axs)):
                self.axs[i].lines.remove(self.SErr2Lines[i])
        except:
            pass
        try:
            del self.SErr2Lines
        except:
            pass
    
    def drawMagMinCross1(self):
        if not self.dicts[self.stPt].has_key('MagMin1') or len(self.axs) < 2:
            return
        #we have to force the graph to the old axes limits because of the completely new line object creation
        xlims = list(self.axs[0].get_xlim())
        ylims = list(self.axs[0].get_ylim())
        self.MagMinCross1 = self.axs[1].plot([self.dicts[self.stPt]['MagMin1T']] ,
                                   [self.dicts[self.stPt]['MagMin1']] ,
                                   markersize = self.magMarkerSize ,
                                   markeredgewidth = self.magMarkerEdgeWidth ,
                                   color = self.dictPhaseColors['Mag'],
                                   marker = self.magMinMarker, zorder=2000)[0]
        self.axs[0].set_xlim(xlims)
        self.axs[0].set_ylim(ylims)
    
    def delMagMinCross1(self):
        try:
            self.axs[1].lines.remove(self.MagMinCross1)
        except:
            pass
    
    def drawMagMaxCross1(self):
        if not self.dicts[self.stPt].has_key('MagMax1') or len(self.axs) < 2:
            return
        #we have to force the graph to the old axes limits because of the completely new line object creation
        xlims = list(self.axs[0].get_xlim())
        ylims = list(self.axs[0].get_ylim())
        self.MagMaxCross1 = self.axs[1].plot([self.dicts[self.stPt]['MagMax1T']],
                                   [self.dicts[self.stPt]['MagMax1']],
                                   markersize = self.magMarkerSize,
                                   markeredgewidth = self.magMarkerEdgeWidth,
                                   color = self.dictPhaseColors['Mag'],
                                   marker = self.magMaxMarker, zorder=2000)[0]
        self.axs[0].set_xlim(xlims)
        self.axs[0].set_ylim(ylims)
    
    def delMagMaxCross1(self):
        try:
            self.axs[1].lines.remove(self.MagMaxCross1)
        except:
            pass
    
    def drawMagMinCross2(self):
        if not self.dicts[self.stPt].has_key('MagMin2') or len(self.axs) < 3:
            return
        #we have to force the graph to the old axes limits because of the completely new line object creation
        xlims = list(self.axs[0].get_xlim())
        ylims = list(self.axs[0].get_ylim())
        self.MagMinCross2 = self.axs[2].plot([self.dicts[self.stPt]['MagMin2T']] ,
                                   [self.dicts[self.stPt]['MagMin2']] ,
                                   markersize = self.magMarkerSize ,
                                   markeredgewidth = self.magMarkerEdgeWidth ,
                                   color = self.dictPhaseColors['Mag'],
                                   marker = self.magMinMarker, zorder=2000)[0]
        self.axs[0].set_xlim(xlims)
        self.axs[0].set_ylim(ylims)
    
    def delMagMinCross2(self):
        try:
            self.axs[2].lines.remove(self.MagMinCross2)
        except:
            pass
    
    def drawMagMaxCross2(self):
        if not self.dicts[self.stPt].has_key('MagMax2') or len(self.axs) < 3:
            return
        #we have to force the graph to the old axes limits because of the completely new line object creation
        xlims = list(self.axs[0].get_xlim())
        ylims = list(self.axs[0].get_ylim())
        self.MagMaxCross2 = self.axs[2].plot([self.dicts[self.stPt]['MagMax2T']],
                                   [self.dicts[self.stPt]['MagMax2']],
                                   markersize = self.magMarkerSize,
                                   markeredgewidth = self.magMarkerEdgeWidth,
                                   color = self.dictPhaseColors['Mag'],
                                   marker = self.magMaxMarker, zorder=2000)[0]
        self.axs[0].set_xlim(xlims)
        self.axs[0].set_ylim(ylims)
    
    def delMagMaxCross2(self):
        try:
            self.axs[2].lines.remove(self.MagMaxCross2)
        except:
            pass
    
    def delP(self):
        try:
            del self.dicts[self.stPt]['P']
            print "P Pick deleted"
        except:
            pass
            
    def delPsynth(self):
        try:
            del self.dicts[self.stPt]['Psynth']
            print "synthetic P Pick deleted"
        except:
            pass
            
    def delPWeight(self):
        try:
            del self.dicts[self.stPt]['PWeight']
            print "P Pick weight deleted"
        except:
            pass
            
    def delPPol(self):
        try:
            del self.dicts[self.stPt]['PPol']
            print "P Pick polarity deleted"
        except:
            pass
            
    def delPOnset(self):
        try:
            del self.dicts[self.stPt]['POnset']
            print "P Pick onset deleted"
        except:
            pass
            
    def delPErr1(self):
        try:
            del self.dicts[self.stPt]['PErr1']
            print "PErr1 Pick deleted"
        except:
            pass
            
    def delPErr2(self):
        try:
            del self.dicts[self.stPt]['PErr2']
            print "PErr2 Pick deleted"
        except:
            pass
            
    def delS(self):
        try:
            del self.dicts[self.stPt]['S']
            del self.dicts[self.stPt]['Saxind']
            print "S Pick deleted"
        except:
            pass
            
    def delSsynth(self):
        try:
            del self.dicts[self.stPt]['Ssynth']
            print "synthetic S Pick deleted"
        except:
            pass
            
    def delSWeight(self):
        try:
            del self.dicts[self.stPt]['SWeight']
            print "S Pick weight deleted"
        except:
            pass
            
    def delSPol(self):
        try:
            del self.dicts[self.stPt]['SPol']
            print "S Pick polarity deleted"
        except:
            pass
            
    def delSOnset(self):
        try:
            del self.dicts[self.stPt]['SOnset']
            print "S Pick onset deleted"
        except:
            pass
            
    def delSErr1(self):
        try:
            del self.dicts[self.stPt]['SErr1']
            print "SErr1 Pick deleted"
        except:
            pass
            
    def delSErr2(self):
        try:
            del self.dicts[self.stPt]['SErr2']
            print "SErr2 Pick deleted"
        except:
            pass
            
    def delMagMin1(self):
        try:
            del self.dicts[self.stPt]['MagMin1']
            del self.dicts[self.stPt]['MagMin1T']
            print "Magnitude Minimum Estimation Pick deleted"
        except:
            pass
            
    def delMagMax1(self):
        try:
            del self.dicts[self.stPt]['MagMax1']
            del self.dicts[self.stPt]['MagMax1T']
            print "Magnitude Maximum Estimation Pick deleted"
        except:
            pass
            
    def delMagMin2(self):
        try:
            del self.dicts[self.stPt]['MagMin2']
            del self.dicts[self.stPt]['MagMin2T']
            print "Magnitude Minimum Estimation Pick deleted"
        except:
            pass
            
    def delMagMax2(self):
        try:
            del self.dicts[self.stPt]['MagMax2']
            del self.dicts[self.stPt]['MagMax2T']
            print "Magnitude Maximum Estimation Pick deleted"
        except:
            pass
            
    
    def delAxes(self):
        for a in self.axs:
            try:
                self.fig.delaxes(a)
                del a
            except:
                pass
        try:
            self.fig.texts.remove(self.supTit)
        except:
            pass
    
    def addFiltButtons(self):
        #add filter buttons
        self.axFilt = self.fig.add_axes([0.22, 0.02, 0.15, 0.15],frameon=False,axisbg='lightgrey')
        self.check = CheckButtons(self.axFilt, ('Filter','Zero-Phase','Spectrogram'),(self.flagFilt,self.flagFiltZPH,self.flagSpectrogram))
        self.check.on_clicked(self.funcFilt)
        self.axFiltTyp = self.fig.add_axes([0.40, 0.02, 0.15, 0.15],frameon=False,axisbg='lightgrey')
        self.radio = RadioButtons(self.axFiltTyp, ('Bandpass', 'Bandstop', 'Lowpass', 'Highpass'),activecolor='k')
        self.radio.on_clicked(self.funcFiltTyp)
        
    def addPhaseButtons(self):
        #add phase buttons
        self.axPhase = self.fig.add_axes([0.10, 0.02, 0.10, 0.15],frameon=False,axisbg='lightgrey')
        self.radioPhase = RadioButtons(self.axPhase, ('P', 'S', 'Mag'),activecolor='k')
        self.radioPhase.on_clicked(self.funcPhase)
        
    def updateLow(self,val):
        if not self.flagFilt or self.flagFiltTyp == 2:
            return
        else:
            self.updatePlot()
    
    def updateHigh(self,val):
        if not self.flagFilt or self.flagFiltTyp == 3:
            return
        else:
            self.updatePlot()
    
    def delSliders(self):
        self.valFiltLow = self.slideLow.val
        self.valFiltHigh = self.slideHigh.val
        try:
            self.fig.delaxes(self.axLowcut)
            self.fig.delaxes(self.axHighcut)
        except:
            return
    
    def addSliders(self):
        #add filter slider
        self.axLowcut = self.fig.add_axes([0.63, 0.05, 0.30, 0.03], xscale='log')
        self.axHighcut  = self.fig.add_axes([0.63, 0.10, 0.30, 0.03], xscale='log')
        low  = 1.0/ (self.streams[self.stPt][0].stats.npts/float(self.streams[self.stPt][0].stats.sampling_rate))
        high = self.streams[self.stPt][0].stats.sampling_rate/2.0
        self.valFiltLow = max(low,self.valFiltLow)
        self.valFiltHigh = min(high,self.valFiltHigh)
        self.slideLow = Slider(self.axLowcut, 'Lowcut', low, high, valinit=self.valFiltLow, facecolor='darkgrey', edgecolor='k', linewidth=1.7)
        self.slideHigh = Slider(self.axHighcut, 'Highcut', low, high, valinit=self.valFiltHigh, facecolor='darkgrey', edgecolor='k', linewidth=1.7)
        self.slideLow.on_changed(self.updateLow)
        self.slideHigh.on_changed(self.updateHigh)
        
    
    def redraw(self):
        for line in self.multicursor.lines:
            line.set_visible(False)
        self.fig.canvas.draw()
    
    def updatePlot(self):
        filt=[]
        #filter data
        if self.flagFilt==True:
            if self.flagFiltZPH:
                if self.flagFiltTyp==0:
                    for tr in self.streams[self.stPt].traces:
                        filt.append(bandpassZPHSH(tr.data,self.slideLow.val,self.slideHigh.val,df=tr.stats.sampling_rate))
                    print "Zero-Phase Bandpass: %.2f-%.2f Hz"%(self.slideLow.val,self.slideHigh.val)
                if self.flagFiltTyp==1:
                    for tr in self.streams[self.stPt].traces:
                        filt.append(bandstopZPHSH(tr.data,self.slideLow.val,self.slideHigh.val,df=tr.stats.sampling_rate))
                    print "Zero-Phase Bandstop: %.2f-%.2f Hz"%(self.slideLow.val,self.slideHigh.val)
                if self.flagFiltTyp==2:
                    for tr in self.streams[self.stPt].traces:
                        filt.append(lowpassZPHSH(tr.data,self.slideHigh.val,df=tr.stats.sampling_rate))
                    print "Zero-Phase Lowpass: %.2f Hz"%(self.slideHigh.val)
                if self.flagFiltTyp==3:
                    for tr in self.streams[self.stPt].traces:
                        filt.append(highpassZPHSH(tr.data,self.slideLow.val,df=tr.stats.sampling_rate))
                    print "Zero-Phase Highpass: %.2f Hz"%(self.slideLow.val)
            else:
                if self.flagFiltTyp==0:
                    for tr in self.streams[self.stPt].traces:
                        filt.append(bandpass(tr.data,self.slideLow.val,self.slideHigh.val,df=tr.stats.sampling_rate))
                    print "One-Pass Bandpass: %.2f-%.2f Hz"%(self.slideLow.val,self.slideHigh.val)
                if self.flagFiltTyp==1:
                    for tr in self.streams[self.stPt].traces:
                        filt.append(bandstop(tr.data,self.slideLow.val,self.slideHigh.val,df=tr.stats.sampling_rate))
                    print "One-Pass Bandstop: %.2f-%.2f Hz"%(self.slideLow.val,self.slideHigh.val)
                if self.flagFiltTyp==2:
                    for tr in self.streams[self.stPt].traces:
                        filt.append(lowpass(tr.data,self.slideHigh.val,df=tr.stats.sampling_rate))
                    print "One-Pass Lowpass: %.2f Hz"%(self.slideHigh.val)
                if self.flagFiltTyp==3:
                    for tr in self.streams[self.stPt].traces:
                        filt.append(highpass(tr.data,self.slideLow.val,df=tr.stats.sampling_rate))
                    print "One-Pass Highpass: %.2f Hz"%(self.slideLow.val)
            #make new plots
            for i in range(len(self.plts)):
                self.plts[i].set_data(self.t, filt[i])
        else:
            #make new plots
            for i in range(len(self.plts)):
                self.plts[i].set_data(self.t, self.streams[self.stPt][i].data)
            print "Unfiltered Traces"
        # Update all subplots
        self.redraw()
    
    def funcFilt(self, label):
        if label=='Filter':
            self.switch_flagFilt()
            self.updatePlot()
        elif label=='Zero-Phase':
            self.switch_flagFiltZPH()
            if self.flagFilt:
                self.updatePlot()
        elif label=='Spectrogram':
            self.flagSpectrogram = not self.flagSpectrogram
            self.delAxes()
            self.drawAxes()
            self.fig.canvas.draw()
    
    def funcFiltTyp(self, label):
        self.flagFiltTyp=self.dictFiltTyp[label]
        if self.flagFilt:
            self.updatePlot()
    
    def funcPhase(self, label):
        self.flagPhase=self.dictPhase[label]
        self.pickingColor=self.dictPhaseColors[label]
        for l in self.multicursor.lines:
            l.set_color(self.pickingColor)
        self.radioPhase.circles[self.flagPhase].set_facecolor(self.pickingColor)
        self.redraw()
    
    def funcSwitchPhase(self):
        self.radioPhase.circles[self.flagPhase].set_facecolor(self.axPhase._axisbg)
        self.flagPhase=(self.flagPhase+1)%len(self.dictPhase)
        self.pickingColor=self.dictPhaseColors[self.dictPhaseInverse[self.flagPhase]]
        for l in self.multicursor.lines:
            l.set_color(self.pickingColor)
        self.radioPhase.circles[self.flagPhase].set_facecolor(self.pickingColor)
        self.redraw()
    
    
    
    
    # Define the event that handles the setting of P- and S-wave picks
    def pick(self, event):
        # Set new P Pick
        if self.flagPhase==0 and event.key==self.dictKeybindings['setPick']:
            self.delPLine()
            self.delPLabel()
            self.delPsynthLine()
            self.dicts[self.stPt]['P']=int(round(event.xdata))
            self.drawPLine()
            self.drawPLabel()
            self.drawPsynthLine()
            self.drawPsynthLabel()
            #check if the new P pick lies outside of the Error Picks
            try:
                if self.dicts[self.stPt]['P']<self.dicts[self.stPt]['PErr1']:
                    self.delPErr1Line()
                    self.delPErr1()
            except:
                pass
            try:
                if self.dicts[self.stPt]['P']>self.dicts[self.stPt]['PErr2']:
                    self.delPErr2Line()
                    self.delPErr2()
            except:
                pass
            # Update all subplots
            self.redraw()
            # Console output
            print "P Pick set at %i"%self.dicts[self.stPt]['P']
        # Set P Pick weight
        if self.dicts[self.stPt].has_key('P'):
            if self.flagPhase==0 and event.key==self.dictKeybindings['setPWeight0']:
                self.delPLabel()
                self.dicts[self.stPt]['PWeight']=0
                self.drawPLabel()
                self.redraw()
                print "P Pick weight set to %i"%self.dicts[self.stPt]['PWeight']
            if self.flagPhase==0 and event.key==self.dictKeybindings['setPWeight1']:
                self.delPLabel()
                self.dicts[self.stPt]['PWeight']=1
                print "P Pick weight set to %i"%self.dicts[self.stPt]['PWeight']
                self.drawPLabel()
                self.redraw()
            if self.flagPhase==0 and event.key==self.dictKeybindings['setPWeight2']:
                self.delPLabel()
                self.dicts[self.stPt]['PWeight']=2
                print "P Pick weight set to %i"%self.dicts[self.stPt]['PWeight']
                self.drawPLabel()
                self.redraw()
            if self.flagPhase==0 and event.key==self.dictKeybindings['setPWeight3']:
                self.delPLabel()
                self.dicts[self.stPt]['PWeight']=3
                print "P Pick weight set to %i"%self.dicts[self.stPt]['PWeight']
                self.drawPLabel()
                self.redraw()
        # Set P Pick polarity
        if self.dicts[self.stPt].has_key('P'):
            if self.flagPhase==0 and event.key==self.dictKeybindings['setPPolUp']:
                self.delPLabel()
                self.dicts[self.stPt]['PPol']='up'
                self.drawPLabel()
                self.redraw()
                print "P Pick polarity set to %s"%self.dicts[self.stPt]['PPol']
            if self.flagPhase==0 and event.key==self.dictKeybindings['setPPolPoorUp']:
                self.delPLabel()
                self.dicts[self.stPt]['PPol']='poorup'
                self.drawPLabel()
                self.redraw()
                print "P Pick polarity set to %s"%self.dicts[self.stPt]['PPol']
            if self.flagPhase==0 and event.key==self.dictKeybindings['setPPolDown']:
                self.delPLabel()
                self.dicts[self.stPt]['PPol']='down'
                self.drawPLabel()
                self.redraw()
                print "P Pick polarity set to %s"%self.dicts[self.stPt]['PPol']
            if self.flagPhase==0 and event.key==self.dictKeybindings['setPPolPoorDown']:
                self.delPLabel()
                self.dicts[self.stPt]['PPol']='poordown'
                self.drawPLabel()
                self.redraw()
                print "P Pick polarity set to %s"%self.dicts[self.stPt]['PPol']
        # Set P Pick onset
        if self.dicts[self.stPt].has_key('P'):
            if self.flagPhase == 0 and event.key == self.dictKeybindings['setPOnsetImpulsive']:
                self.delPLabel()
                self.dicts[self.stPt]['POnset'] = 'impulsive'
                self.drawPLabel()
                self.redraw()
                print "P pick onset set to %s" % self.dicts[self.stPt]['POnset']
            elif self.flagPhase == 0 and event.key == self.dictKeybindings['setPOnsetEmergent']:
                self.delPLabel()
                self.dicts[self.stPt]['POnset'] = 'emergent'
                self.drawPLabel()
                self.redraw()
                print "P pick onset set to %s" % self.dicts[self.stPt]['POnset']
        # Set new S Pick
        if self.flagPhase==1 and event.key==self.dictKeybindings['setPick']:
            self.delSLine()
            self.delSLabel()
            self.delSsynthLine()
            self.dicts[self.stPt]['S'] = int(round(event.xdata))
            self.dicts[self.stPt]['Saxind'] = self.axs.index(event.inaxes)
            #self.dicts[self.stPt]['Saxind'] = int(round(event.xdata))
            self.drawSLine()
            self.drawSLabel()
            self.drawSsynthLine()
            self.drawSsynthLabel()
            #check if the new S pick lies outside of the Error Picks
            try:
                if self.dicts[self.stPt]['S']<self.dicts[self.stPt]['SErr1']:
                    self.delSErr1Line()
                    self.delSErr1()
            except:
                pass
            try:
                if self.dicts[self.stPt]['S']>self.dicts[self.stPt]['SErr2']:
                    self.delSErr2Line()
                    self.delSErr2()
            except:
                pass
            # Update all subplots
            self.redraw()
            # Console output
            print "S Pick set at %i"%self.dicts[self.stPt]['S']
        # Set S Pick weight
        if self.dicts[self.stPt].has_key('S'):
            if self.flagPhase==1 and event.key==self.dictKeybindings['setSWeight0']:
                self.delSLabel()
                self.dicts[self.stPt]['SWeight']=0
                self.drawSLabel()
                self.redraw()
                print "S Pick weight set to %i"%self.dicts[self.stPt]['SWeight']
            if self.flagPhase==1 and event.key==self.dictKeybindings['setSWeight1']:
                self.delSLabel()
                self.dicts[self.stPt]['SWeight']=1
                self.drawSLabel()
                self.redraw()
                print "S Pick weight set to %i"%self.dicts[self.stPt]['SWeight']
            if self.flagPhase==1 and event.key==self.dictKeybindings['setSWeight2']:
                self.delSLabel()
                self.dicts[self.stPt]['SWeight']=2
                self.drawSLabel()
                self.redraw()
                print "S Pick weight set to %i"%self.dicts[self.stPt]['SWeight']
            if self.flagPhase==1 and event.key==self.dictKeybindings['setSWeight3']:
                self.delSLabel()
                self.dicts[self.stPt]['SWeight']=3
                self.drawSLabel()
                self.redraw()
                print "S Pick weight set to %i"%self.dicts[self.stPt]['SWeight']
        # Set S Pick polarity
        if self.dicts[self.stPt].has_key('S'):
            if self.flagPhase==1 and event.key==self.dictKeybindings['setSPolUp']:
                self.delSLabel()
                self.dicts[self.stPt]['SPol']='up'
                self.drawSLabel()
                self.redraw()
                print "S Pick polarity set to %s"%self.dicts[self.stPt]['SPol']
            if self.flagPhase==1 and event.key==self.dictKeybindings['setSPolPoorUp']:
                self.delSLabel()
                self.dicts[self.stPt]['SPol']='poorup'
                self.drawSLabel()
                self.redraw()
                print "S Pick polarity set to %s"%self.dicts[self.stPt]['SPol']
            if self.flagPhase==1 and event.key==self.dictKeybindings['setSPolDown']:
                self.delSLabel()
                self.dicts[self.stPt]['SPol']='down'
                self.drawSLabel()
                self.redraw()
                print "S Pick polarity set to %s"%self.dicts[self.stPt]['SPol']
            if self.flagPhase==1 and event.key==self.dictKeybindings['setSPolPoorDown']:
                self.delSLabel()
                self.dicts[self.stPt]['SPol']='poordown'
                self.drawSLabel()
                self.redraw()
                print "S Pick polarity set to %s"%self.dicts[self.stPt]['SPol']
        # Set S Pick onset
        if self.dicts[self.stPt].has_key('S'):
            if self.flagPhase == 1 and event.key == self.dictKeybindings['setSOnsetImpulsive']:
                self.delSLabel()
                self.dicts[self.stPt]['SOnset'] = 'impulsive'
                self.drawSLabel()
                self.redraw()
                print "S pick onset set to %s" % self.dicts[self.stPt]['SOnset']
            elif self.flagPhase == 1 and event.key == self.dictKeybindings['setSOnsetEmergent']:
                self.delSLabel()
                self.dicts[self.stPt]['SOnset'] = 'emergent'
                self.drawSLabel()
                self.redraw()
                print "S pick onset set to %s" % self.dicts[self.stPt]['SOnset']
        # Remove P Pick
        if self.flagPhase==0 and event.key==self.dictKeybindings['delPick']:
            # Try to remove all existing Pick lines and P Pick variable
            self.delPLine()
            self.delP()
            self.delPWeight()
            self.delPPol()
            self.delPOnset()
            self.delPLabel()
            # Try to remove existing Pick Error 1 lines and variable
            self.delPErr1Line()
            self.delPErr1()
            # Try to remove existing Pick Error 2 lines and variable
            self.delPErr2Line()
            self.delPErr2()
            # Update all subplots
            self.redraw()
        # Remove S Pick
        if self.flagPhase==1 and event.key==self.dictKeybindings['delPick']:
            # Try to remove all existing Pick lines and P Pick variable
            self.delSLine()
            self.delS()
            self.delSWeight()
            self.delSPol()
            self.delSOnset()
            self.delSLabel()
            # Try to remove existing Pick Error 1 lines and variable
            self.delSErr1Line()
            self.delSErr1()
            # Try to remove existing Pick Error 2 lines and variable
            self.delSErr2Line()
            self.delSErr2()
            # Update all subplots
            self.redraw()
        # Set new P Pick uncertainties
        if self.flagPhase==0 and event.key==self.dictKeybindings['setPickError']:
            # Set Flag to determine scenario
            try:
                # Set left Error Pick
                if event.xdata<self.dicts[self.stPt]['P']:
                    errFlag=1
                # Set right Error Pick
                else:
                    errFlag=2
            # Set no Error Pick (no P Pick yet)
            except:
                errFlag=0
            # Case 1
            if errFlag==1:
                # Define global variables seen outside
                # Remove old lines from the plot before plotting the new ones
                self.delPErr1Line()
                # Save sample value of error pick (round to integer sample value)
                self.dicts[self.stPt]['PErr1']=int(round(event.xdata))
                # Plot the lines for the P Error pick in all three traces
                self.drawPErr1Line()
                # Update all subplots
                self.redraw()
                # Console output
                print "P Error Pick 1 set at %i"%self.dicts[self.stPt]['PErr1']
            # Case 2
            if errFlag==2:
                # Define global variables seen outside
                # Remove old lines from the plot before plotting the new ones
                self.delPErr2Line()
                # Save sample value of error pick (round to integer sample value)
                self.dicts[self.stPt]['PErr2']=int(round(event.xdata))
                # Plot the lines for the P Error pick in all three traces
                self.drawPErr2Line()
                # Update all subplots
                self.redraw()
                # Console output
                print "P Error Pick 2 set at %i"%self.dicts[self.stPt]['PErr2']
        # Set new S Pick uncertainties
        if self.flagPhase==1 and event.key==self.dictKeybindings['setPickError']:
            # Set Flag to determine scenario
            try:
                # Set left Error Pick
                if event.xdata<self.dicts[self.stPt]['S']:
                    errFlag=1
                # Set right Error Pick
                else:
                    errFlag=2
            # Set no Error Pick (no S Pick yet)
            except:
                errFlag=0
            # Case 1
            if errFlag==1:
                # Define global variables seen outside
                # Remove old lines from the plot before plotting the new ones
                self.delSErr1Line()
                # Save sample value of error pick (round to integer sample value)
                self.dicts[self.stPt]['SErr1']=int(round(event.xdata))
                # Plot the lines for the S Error pick in all three traces
                self.drawSErr1Line()
                # Update all subplots
                self.redraw()
                # Console output
                print "S Error Pick 1 set at %i"%self.dicts[self.stPt]['SErr1']
            # Case 2
            if errFlag==2:
                # Define global variables seen outside
                # Remove old lines from the plot before plotting the new ones
                self.delSErr2Line()
                # Save sample value of error pick (round to integer sample value)
                self.dicts[self.stPt]['SErr2']=int(round(event.xdata))
                # Plot the lines for the S Error pick in all three traces
                self.drawSErr2Line()
                # Update all subplots
                self.redraw()
                # Console output
                print "S Error Pick 2 set at %i"%self.dicts[self.stPt]['SErr2']
        # Magnitude estimation picking:
        if self.flagPhase==2 and event.key==self.dictKeybindings['setMagMin'] and len(self.axs) > 2:
            if event.inaxes == self.axs[1]:
                self.delMagMinCross1()
                xpos=int(event.xdata)
                ydata=event.inaxes.lines[0].get_ydata() #get the first line hoping that it is the seismogram!
                cutoffSamples=xpos-self.magPickWindow #remember, how much samples there are before our small window! We have to add this number for our MagMinT estimation!
                self.dicts[self.stPt]['MagMin1']=np.min(ydata[xpos-self.magPickWindow:xpos+self.magPickWindow])
                self.dicts[self.stPt]['MagMin1T']=cutoffSamples+np.argmin(ydata[xpos-self.magPickWindow:xpos+self.magPickWindow])
                #delete old MagMax Pick, if new MagMin Pick is higher
                try:
                    if self.dicts[self.stPt]['MagMin1'] > self.dicts[self.stPt]['MagMax1']:
                        self.delMagMaxCross1()
                        self.delMagMax1()
                except:
                    pass
                self.drawMagMinCross1()
                self.redraw()
                print "Minimum for magnitude estimation set: %s at %s"%(self.dicts[self.stPt]['MagMin1'],self.dicts[self.stPt]['MagMin1T'])
            elif event.inaxes == self.axs[2]:
                self.delMagMinCross2()
                xpos=int(event.xdata)
                ydata=event.inaxes.lines[0].get_ydata() #get the first line hoping that it is the seismogram!
                cutoffSamples=xpos-self.magPickWindow #remember, how much samples there are before our small window! We have to add this number for our MagMinT estimation!
                self.dicts[self.stPt]['MagMin2']=np.min(ydata[xpos-self.magPickWindow:xpos+self.magPickWindow])
                self.dicts[self.stPt]['MagMin2T']=cutoffSamples+np.argmin(ydata[xpos-self.magPickWindow:xpos+self.magPickWindow])
                #delete old MagMax Pick, if new MagMin Pick is higher
                try:
                    if self.dicts[self.stPt]['MagMin2'] > self.dicts[self.stPt]['MagMax2']:
                        self.delMagMaxCross2()
                        self.delMagMax2()
                except:
                    pass
                self.drawMagMinCross2()
                self.redraw()
                print "Minimum for magnitude estimation set: %s at %s"%(self.dicts[self.stPt]['MagMin2'],self.dicts[self.stPt]['MagMin2T'])
        if self.flagPhase==2 and event.key==self.dictKeybindings['setMagMax'] and len(self.axs) > 2:
            if event.inaxes == self.axs[1]:
                self.delMagMaxCross1()
                xpos=int(event.xdata)
                ydata=event.inaxes.lines[0].get_ydata() #get the first line hoping that it is the seismogram!
                cutoffSamples=xpos-self.magPickWindow #remember, how much samples there are before our small window! We have to add this number for our MagMinT estimation!
                self.dicts[self.stPt]['MagMax1']=np.max(ydata[xpos-self.magPickWindow:xpos+self.magPickWindow])
                self.dicts[self.stPt]['MagMax1T']=cutoffSamples+np.argmax(ydata[xpos-self.magPickWindow:xpos+self.magPickWindow])
                #delete old MagMax Pick, if new MagMax Pick is higher
                try:
                    if self.dicts[self.stPt]['MagMin1'] > self.dicts[self.stPt]['MagMax1']:
                        self.delMagMinCross1()
                        self.delMagMin1()
                except:
                    pass
                self.drawMagMaxCross1()
                self.redraw()
                print "Maximum for magnitude estimation set: %s at %s"%(self.dicts[self.stPt]['MagMax1'],self.dicts[self.stPt]['MagMax1T'])
            elif event.inaxes == self.axs[2]:
                self.delMagMaxCross2()
                xpos=int(event.xdata)
                ydata=event.inaxes.lines[0].get_ydata() #get the first line hoping that it is the seismogram!
                cutoffSamples=xpos-self.magPickWindow #remember, how much samples there are before our small window! We have to add this number for our MagMinT estimation!
                self.dicts[self.stPt]['MagMax2']=np.max(ydata[xpos-self.magPickWindow:xpos+self.magPickWindow])
                self.dicts[self.stPt]['MagMax2T']=cutoffSamples+np.argmax(ydata[xpos-self.magPickWindow:xpos+self.magPickWindow])
                #delete old MagMax Pick, if new MagMax Pick is higher
                try:
                    if self.dicts[self.stPt]['MagMin2'] > self.dicts[self.stPt]['MagMax2']:
                        self.delMagMinCross2()
                        self.delMagMin2()
                except:
                    pass
                self.drawMagMaxCross2()
                self.redraw()
                print "Maximum for magnitude estimation set: %s at %s"%(self.dicts[self.stPt]['MagMax2'],self.dicts[self.stPt]['MagMax2T'])
        if self.flagPhase == 2 and event.key == self.dictKeybindings['delMagMinMax']:
            if event.inaxes == self.axs[1]:
                self.delMagMaxCross1()
                self.delMagMinCross1()
                self.delMagMin1()
                self.delMagMax1()
            elif event.inaxes == self.axs[2]:
                self.delMagMaxCross2()
                self.delMagMinCross2()
                self.delMagMin2()
                self.delMagMax2()
            else:
                return
            self.redraw()
    
    # Define zoom events for the mouse scroll wheel
    def zoom(self,event):
        # Zoom in on scroll-up
        if event.button=='up' and self.flagWheelZoom:
            # Calculate and set new axes boundaries from old ones
            (left,right)=self.axs[0].get_xbound()
            left+=(event.xdata-left)/2
            right-=(right-event.xdata)/2
            self.axs[0].set_xbound(lower=left,upper=right)
            # Update all subplots
            self.redraw()
        # Zoom out on scroll-down
        if event.button=='down' and self.flagWheelZoom:
            # Calculate and set new axes boundaries from old ones
            (left,right)=self.axs[0].get_xbound()
            left-=(event.xdata-left)/2
            right+=(right-event.xdata)/2
            self.axs[0].set_xbound(lower=left,upper=right)
            # Update all subplots
            self.redraw()
    
    # Define zoom reset for the mouse button 2 (always scroll wheel!?)
    def zoom_reset(self,event):
        if event.button==2:
            # Use Z trace limits as boundaries
            self.axs[0].set_xbound(lower=self.xMin,upper=self.xMax)
            self.axs[0].set_ybound(lower=self.yMin,upper=self.yMax)
            # Update all subplots
            self.redraw()
            print "Resetting axes"
    
    def switchWheelZoom(self,event):
        if event.key==self.dictKeybindings['switchWheelZoom']:
            self.flagWheelZoom=not self.flagWheelZoom
            if self.flagWheelZoom:
                print "Mouse wheel zooming activated"
            else:
                print "Mouse wheel zooming deactivated"
    
    def switchPan(self,event):
        if event.key==self.dictKeybindings['switchPan']:
            self.fig.canvas.toolbar.pan()
            self.fig.canvas.widgetlock.release(self.fig.canvas.toolbar)
            self.redraw()
            print "Switching pan mode"
    
    #lookup multicursor source: http://matplotlib.sourcearchive.com/documentation/0.98.1/widgets_8py-source.html
    def multicursorReinit(self):
        self.fig.canvas.mpl_disconnect(self.multicursor.id1)
        self.fig.canvas.mpl_disconnect(self.multicursor.id2)
        self.multicursor.__init__(self.fig.canvas,self.axs, useblit=True, color='black', linewidth=1, ls='dotted')
        #fig.canvas.draw_idle()
        #multicursor._update()
        #multicursor.needclear=True
        #multicursor.background = fig.canvas.copy_from_bbox(fig.canvas.figure.bbox)
        #fig.canvas.restore_region(multicursor.background)
        #fig.canvas.blit(fig.canvas.figure.bbox)
        for l in self.multicursor.lines:
            l.set_color(self.pickingColor)
    
    def switchPhase(self, event):
        if event.key==self.dictKeybindings['switchPhase']:
            self.funcSwitchPhase()
            print "Switching Phase button"
            
    def switchStream(self, event):
        if event.key==self.dictKeybindings['prevStream']:
            self.stPt=(self.stPt-1)%self.stNum
            xmin, xmax = self.axs[0].get_xlim()
            self.delAxes()
            self.drawAxes()
            self.drawSavedPicks()
            self.delSliders()
            self.addSliders()
            self.multicursorReinit()
            self.axs[0].set_xlim(xmin, xmax)
            self.updatePlot()
            print "Going to previous stream"
        if event.key==self.dictKeybindings['nextStream']:
            self.stPt=(self.stPt+1)%self.stNum
            xmin, xmax = self.axs[0].get_xlim()
            self.delAxes()
            self.drawAxes()
            self.drawSavedPicks()
            self.delSliders()
            self.addSliders()
            self.multicursorReinit()
            self.axs[0].set_xlim(xmin, xmax)
            self.updatePlot()
            print "Going to next stream"
            
    def blockRedraw(self, event):
        if event.button==1 or event.button==3:
            self.multicursor.visible=False
            self.fig.canvas.widgetlock(self.fig.canvas.toolbar)
            
    def allowRedraw(self, event):
        if event.button==1 or event.button==3:
            self.multicursor.visible=True
            self.fig.canvas.widgetlock.release(self.fig.canvas.toolbar)
    
    def load3dlocSyntheticPhases(self):
        try:
            fhandle = open(self.threeDlocOutfile, 'r')
            phaseList = fhandle.readlines()
            fhandle.close()
        except:
            return
        self.delPsynth()
        self.delSsynth()
        self.delPsynthLine()
        self.delPsynthLabel()
        self.delSsynthLine()
        self.delSsynthLabel()
        for phase in phaseList[1:]:
            # example for a synthetic pick line from 3dloc:
            # RJOB P 2009 12 27 10 52 59.425 -0.004950 298.199524 136.000275
            # station phase YYYY MM DD hh mm ss.sss (picked time!) residual
            # (add this to get synthetic time) azimuth? incidenceangle?
            phase = phase.split()
            phStat = phase[0]
            phType = phase[1]
            phUTCTime = UTCDateTime(int(phase[2]), int(phase[3]),
                                    int(phase[4]), int(phase[5]),
                                    int(phase[6]), float(phase[7]))
            phResid = float(phase[8])
            phUTCTime += phResid
            for i in range(len(self.streams)):
                # check for matching station names
                if not phStat == self.streams[i][0].stats.station.strip():
                    continue
                else:
                    # check if synthetic pick is within time range of stream
                    if (phUTCTime > self.streams[i][0].stats.endtime or
                        phUTCTime < self.streams[i][0].stats.starttime):
                        warnings.warn("Synthetic pick outside timespan.")
                        continue
                    else:
                        # phSamps is the number of samples after the stream-
                        # starttime at which the time of the synthetic phase
                        # is located
                        phSamps = phUTCTime - self.streams[i][0].stats.starttime
                        phSamps = int(round(phSamps *
                                            self.streams[i][0].stats.sampling_rate))
                        if phType == 'P':
                            self.dicts[i]['Psynth'] = phSamps
                            self.dicts[i]['Pres'] = phResid
                        elif phType == 'S':
                            self.dicts[i]['Ssynth'] = phSamps
                            self.dicts[i]['Sres'] = phResid
        self.drawPsynthLine()
        self.drawPsynthLabel()
        self.drawSsynthLine()
        self.drawSsynthLabel()
        self.redraw()

    def do3dLoc(self):
        self.xmlEventID = '%i' % time.time()
        subprocess.call(self.threeDlocPreCall, shell = True)
        f = open(self.threeDlocInfile, 'w')
        network = "BW"
        fmt = "%04s  %s        %s %5.3f -999.0 0.000 -999. 0.000 T__DR_ %9.6f %9.6f %8.6f\n"
        self.coords = []
        for i in range(len(self.streams)):
            #lon, lat, ele = getCoord(network, self.stationlist[i])
            lon = self.dicts[i]['StaLon']
            lat = self.dicts[i]['StaLat']
            ele = self.dicts[i]['StaEle']
            self.coords.append([lon, lat])
            if self.dicts[i].has_key('P'):
                t = self.streams[i][0].stats.starttime
                t += self.dicts[i]['P'] / self.streams[i][0].stats.sampling_rate
                date = t.strftime("%Y %m %d %H %M %S")
                date += ".%03d" % (t.microsecond / 1e3 + 0.5)
                delta = self.dicts[i]['PErr2'] - self.dicts[i]['PErr1']
                delta /= self.streams[i][0].stats.sampling_rate
                f.write(fmt % (self.stationlist[i], 'P', date, delta,
                               lon, lat, ele / 1e3))
            if self.dicts[i].has_key('S'):
                t = self.streams[i][0].stats.starttime
                t += self.dicts[i]['S'] / self.streams[i][0].stats.sampling_rate
                date = t.strftime("%Y %m %d %H %M %S")
                date += ".%03d" % (t.microsecond / 1e3 + 0.5)
                delta = self.dicts[i]['SErr2'] - self.dicts[i]['SErr1']
                delta /= self.streams[i][0].stats.sampling_rate
                f.write(fmt % (self.stationlist[i], 'S', date, delta,
                               lon, lat, ele / 1e3))
        f.close()
        print 'Phases for 3Dloc:'
        self.catFile(self.threeDlocInfile)
        subprocess.call(self.threeDlocCall, shell = True)
        print '--> 3dloc finished'
        self.catFile(self.threeDlocOutfile)

    def doHyp2000(self):
        self.xmlEventID = '%i' % time.time()
        subprocess.call(self.hyp2000PreCall, shell = True)
        f = open(self.hyp2000Phasefile, 'w')
        f2 = open(self.hyp2000Stationsfile, 'w')
        network = "BW"
        #fmt = "RWMOIP?0 091229124412.22       13.99IS?0"
        fmtP = "%4s%1sP%1s%1i %15s"
        fmtS = "%12s%1sS%1s%1i\n"
        #fmt2 = "  BGLD4739.14N01300.75E 930"
        fmt2 = "%6s%2i%5.2fN%3i%5.2fE%4i\n"
        #self.coords = []
        for i in range(len(self.streams)):
            #lon, lat, ele = getCoord(network, self.stationlist[i])
            sta = self.dicts[i]['Station']
            lon = self.dicts[i]['StaLon']
            lon_deg = int(lon)
            lon_min = (lon - lon_deg) * 60.
            lat = self.dicts[i]['StaLat']
            lat_deg = int(lat)
            lat_min = (lat - lat_deg) * 60.
            ele = self.dicts[i]['StaEle'] * 1000
            f2.write(fmt2 % (sta, lon_deg, lon_min, lat_deg, lat_min, ele))
            #self.coords.append([lon, lat])
            if self.dicts[i].has_key('P'):
                t = self.streams[i][0].stats.starttime
                t += self.dicts[i]['P'] / self.streams[i][0].stats.sampling_rate
                date = t.strftime("%y%m%d%H%M%S")
                #print date
                date += ".%02d" % (t.microsecond / 1e4 + 0.5)
                #print t.microsecond
                #print date
                if self.dicts[i].has_key('POnset'):
                    if self.dicts[i]['POnset'] == 'impulsive':
                        onset = 'I'
                    elif self.dicts[i]['POnset'] == 'emergent':
                        onset = 'E'
                    else: #XXX check for other names correctly!!!
                        onset = 'I'
                else:
                    onset = 'I'
                if self.dicts[i].has_key('PPol'):
                    if self.dicts[i]['PPol'] == "up" or \
                       self.dicts[i]['PPol'] == "poorup":
                        polarity = "U"
                    elif self.dicts[i]['PPol'] == "down" or \
                         self.dicts[i]['PPol'] == "poordown":
                        polarity = "D"
                    else: #XXX check for other names correctly!!!
                        polarity = "D"
                else:
                    polarity = "?"
                if self.dicts[i].has_key('PWeight'):
                    weight = int(self.dicts[i]['PWeight'])
                else:
                    weight = 0
                f.write(fmtP % (sta, onset, polarity, weight, date))
            if self.dicts[i].has_key('S'):
                t2 = self.streams[i][0].stats.starttime
                t2 += self.dicts[i]['S'] / self.streams[i][0].stats.sampling_rate
                date2 = t2.strftime("%H%M%S")
                date2 += ".%02d" % (t2.microsecond / 1e4 + 0.5)
                if self.dicts[i].has_key('SOnset'):
                    if self.dicts[i]['SOnset'] == 'impulsive':
                        onset2 = 'I'
                    elif self.dicts[i]['SOnset'] == 'emergent':
                        onset2 = 'E'
                    else: #XXX check for other names correctly!!!
                        onset2 = 'I'
                else:
                    onset2 = 'I'
                if self.dicts[i].has_key('SPol'):
                    if self.dicts[i]['SPol'] == "up" or \
                       self.dicts[i]['SPol'] == "poorup":
                        polarity2 = "U"
                    elif self.dicts[i]['SPol'] == "down" or \
                         self.dicts[i]['SPol'] == "poordown":
                        polarity2 = "D"
                    else: #XXX check for other names correctly!!!
                        polarity2 = "D"
                else:
                    polarity2 = "?"
                if self.dicts[i].has_key('SWeight'):
                    weight2 = int(self.dicts[i]['SWeight'])
                else:
                    weight2 = 0
                f.write(fmtS % (date2, onset2, polarity2, weight2))
            else:
                f.write("\n")
        f.close()
        f2.close()
        print 'Phases for Hypo2000:'
        self.catFile(self.hyp2000Phasefile)
        print 'Stations for Hypo2000:'
        self.catFile(self.hyp2000Stationsfile)
        subprocess.call(self.hyp2000Call, shell = True)
        print '--> hyp2000 finished'
        self.catFile(self.hyp2000Summary)

    def catFile(self, file):
        lines = open(file).readlines()
        for line in lines:
            print line.rstrip()

    def loadHyp2000Data(self):
        #self.load3dlocSyntheticPhases()
        lines = open(self.hyp2000Summary).readlines()
        if lines == []:
            print "Error: Did not find Hypo2000 output file"
            return
        # goto origin info line
        while True:
            try:
                line = lines.pop(0)
            except:
                break
            if line.startswith(" YEAR MO DA  --ORIGIN--"):
                break
        try:
            line = lines.pop(0)
        except:
            print "Error: No location in Hypo2000 Outputfile."
            return

        year = int(line[1:5])
        month = int(line[6:8])
        day = int(line[9:11])
        hour = int(line[13:15])
        minute = int(line[15:17])
        seconds = float(line[18:23])
        time = UTCDateTime(year, month, day, hour, minute, seconds)
        lat_deg = int(line[25:27])
        lat_min = float(line[28:33])
        lat = lat_deg + (lat_min / 60.)
        if line[27] == "S":
            lat = -lat
        lon_deg = int(line[35:38])
        lon_min = float(line[39:44])
        lon = lon_deg + (lon_min / 60.)
        if line[38] == " ":
            lon = -lon
        depth = float(line[46:51])
        rms = float(line[52:57])
        errXY = float(line[58:63])
        errZ = float(line[64:69])

        # goto next origin info line
        while True:
            try:
                line = lines.pop(0)
            except:
                break
            if line.startswith(" NSTA NPHS  DMIN MODEL"):
                break
        line = lines.pop(0)

        model = line[17:22].strip()
        gap = int(line[23:26])

        # assign origin info
        self.EventLon = lon
        self.EventLat = lat
        self.EventZ = depth
        self.EventErrX = errXY
        self.EventErrY = errXY
        self.EventErrZ = errZ
        self.EventStdErr = rms #XXX stimmt diese Zuordnung!!!?!
        self.EventAzimGap = gap
        self.EventUsedModel = model
        self.EventTime = time
        
        # goto station and phases info lines
        while True:
            try:
                line = lines.pop(0)
            except:
                break
            if line.startswith(" STA NET COM L CR DIST AZM"):
                break
        
        self.PCount = 0
        self.SCount = 0
        for i in range(len(lines)):
            # check which type of phase
            if lines[i][32] == "P":
                type = "P"
            elif lines[i][32] == "S":
                type = "S"
            else:
                continue
            # get values from line
            station = lines[i][0:6].strip()
            if station == "":
                station = lines[i-1][0:6].strip()
                azimuth = int(lines[i-1][23:26])
                #XXX check, if incident is correct!!
                incident = int(lines[i-1][27:30])
            else:
                azimuth = int(lines[i][23:26])
                #XXX check, if incident is correct!!
                incident = int(lines[i][27:30])
            if lines[i][31] == "I":
                onset = "impulsive"
            elif lines[i][31] == "E":
                onset = "emergent"
            else:
                onset = None
            if lines[i][33] == "U":
                polarity = "up"
            elif lines[i][33] == "D":
                polarity = "down"
            else:
                polarity = None
            res = float(lines[i][61:66])
            weight = float(lines[i][68:72])

            # search for streamnumber corresponding to pick
            streamnum = None
            for i in range(len(self.streams)):
                if station.strip() != self.dicts[i]['Station']:
                    continue
                else:
                    streamnum = i
                    break
            if streamnum == None:
                message = "Did not find matching stream for pick data " + \
                          "with station id: \"%s\"" % station.strip()
                warnings.warn(message)
                continue
            
            # assign synthetic phase info
            if type == "P":
                self.PCount += 1
                synthsamps = int(round(res *
                        self.streams[i][0].stats.sampling_rate))
                synthsamps += self.dicts[streamnum]['P']
                self.dicts[streamnum]['Psynth'] = synthsamps
                self.dicts[streamnum]['Pres'] = res
                self.dicts[streamnum]['PAzim'] = azimuth
                self.dicts[streamnum]['PInci'] = incident
                if onset:
                    self.dicts[streamnum]['POnset'] = onset
                if polarity:
                    self.dicts[streamnum]['PPol'] = polarity
                #XXX how to set the weight???
                # we use weights 0,1,2,3 but hypo2000 outputs floats...
                #self.dicts[streamnum]['PWeight'] = weight
                self.dicts[streamnum]['PResInfo'] = '\n\n %+0.3fs' % res
                if self.dicts[streamnum].has_key('PPol'):
                    self.dicts[streamnum]['PResInfo'] += '  %s' % \
                            self.dicts[streamnum]['PPol']
            elif type == "S":
                self.SCount += 1
                self.dicts[streamnum]['SLon'] = self.dicts[streamnum]['StaLon']
                self.dicts[streamnum]['SLat'] = self.dicts[streamnum]['StaLat']
                synthsamps = int(round(res *
                        self.streams[i][0].stats.sampling_rate))
                synthsamps += self.dicts[streamnum]['S']
                self.dicts[streamnum]['Ssynth'] = synthsamps
                self.dicts[streamnum]['Sres'] = res
                self.dicts[streamnum]['SAzim'] = azimuth
                self.dicts[streamnum]['SInci'] = incident
                if onset:
                    self.dicts[streamnum]['SOnset'] = onset
                if polarity:
                    self.dicts[streamnum]['SPol'] = polarity
                #XXX how to set the weight???
                # we use weights 0,1,2,3 but hypo2000 outputs floats...
                #self.dicts[streamnum]['SWeight'] = weight
                self.dicts[streamnum]['SResInfo'] = '\n\n\n %+0.3fs' % res
                if self.dicts[streamnum].has_key('SPol'):
                    self.dicts[streamnum]['SResInfo'] += '  %s' % \
                            self.dicts[streamnum]['SPol']
        self.usedStationsCount = len(self.dicts)
        for st in self.dicts:
            if not (st.has_key('Psynth') or st.has_key('Ssynth')):
                self.usedStationsCount -= 1

    def load3dlocData(self):
        #self.load3dlocSyntheticPhases()
        event = open(self.threeDlocOutfile).readline().split()
        self.EventLon = float(event[8])
        self.EventLat = float(event[9])
        self.EventZ = float(event[10])
        self.EventErrX = float(event[11])
        self.EventErrY = float(event[12])
        self.EventErrZ = float(event[13])
        self.EventStdErr = float(event[14])
        self.EventAzimGap = float(event[15])
        self.EventTime = UTCDateTime(int(event[2]), int(event[3]),
                                              int(event[4]), int(event[5]),
                                              int(event[6]), float(event[7]))
        self.PCount = 0
        self.SCount = 0
        lines = open(self.threeDlocInfile).readlines()
        for line in lines:
            pick = line.split()
            for i in range(len(self.streams)):
                if pick[0].strip() == self.streams[i][0].stats.station.strip():
                    if pick[1] == 'P':
                        self.PCount += 1
                    elif pick[1] == 'S':
                        self.SCount += 1
                    break
        lines = open(self.threeDlocOutfile).readlines()
        for line in lines[1:]:
            pick = line.split()
            for i in range(len(self.streams)):
                if pick[0].strip() == self.streams[i][0].stats.station.strip():
                    if pick[1] == 'P':
                        self.dicts[i]['PAzim'] = float(pick[9])
                        self.dicts[i]['PInci'] = float(pick[10])
                        self.dicts[i]['PResInfo'] = '\n\n %+0.3fs' % float(pick[8])
                        if self.dicts[i].has_key('PPol'):
                            self.dicts[i]['PResInfo'] += '  %s' % self.dicts[i]['PPol']
                            
                    elif pick[1] == 'S':
                        self.dicts[i]['SAzim'] = float(pick[9])
                        self.dicts[i]['SInci'] = float(pick[10])
                        self.dicts[i]['SResInfo'] = '\n\n\n %+0.3fs' % float(pick[8])
                        if self.dicts[i].has_key('SPol'):
                            self.dicts[i]['SResInfo'] += '  %s' % self.dicts[i]['SPol']
                    break
        self.usedStationsCount = len(self.dicts)
        for st in self.dicts:
            if not (st.has_key('Psynth') or st.has_key('Ssynth')):
                self.usedStationsCount -= 1
    
    def updateNetworkMag(self):
        print "updating network magnitude..."
        self.staMagCount = 0
        self.netMag = 0
        self.staMags = []
        for i in range(len(self.streams)):
            if self.dicts[i]['MagUse'] and self.dicts[i].has_key('Mag'):
                print self.dicts[i]['Station']
                self.staMagCount += 1
                self.netMag += self.dicts[i]['Mag']
                self.staMags.append(self.dicts[i]['Mag'])
        if self.staMagCount == 0:
            self.netMag = np.nan
        else:
            self.netMag /= self.staMagCount
        self.netMagVar = np.var(self.staMags)
        print "new network magnitude: %.2f (Variance: %.2f)" % (self.netMag, self.netMagVar)
        self.netMagLabel = '\n\n\n\n  %.2f (Var: %.2f)' % (self.netMag, self.netMagVar)
        try:
            self.netMagText.set_text(self.netMagLabel)
        except:
            pass
    
    def calculateEpiHypoDists(self):
        epidists = []
        for i in range(len(self.streams)):
            x, y = utlGeoKm(self.EventLon, self.EventLat,
                            self.dicts[i]['StaLon'], self.dicts[i]['StaLat'])
            z = abs(self.dicts[i]['StaEle'] - self.EventZ)
            self.dicts[i]['distX'] = x
            self.dicts[i]['distY'] = y
            self.dicts[i]['distZ'] = z
            self.dicts[i]['distEpi'] = np.sqrt(x**2 + y**2)
            # Median and Max/Min of epicentral distances should only be used
            # for stations with a pick that goes into the location.
            # The epicentral distance of all other stations may be needed for
            # magnitude estimation nonetheless.
            if self.dicts[i].has_key('Psynth') or self.dicts[i].has_key('Ssynth'):
                epidists.append(self.dicts[i]['distEpi'])
            self.dicts[i]['distHypo'] = np.sqrt(x**2 + y**2 + z**2)
        self.epidistMax = max(epidists)
        self.epidistMin = min(epidists)
        self.epidistMedian = np.median(epidists)

    def calculateStationMagnitudes(self):
        for i in range(len(self.streams)):
            if (self.dicts[i].has_key('MagMin1') and
                self.dicts[i].has_key('MagMin2') and
                self.dicts[i].has_key('MagMax1') and
                self.dicts[i].has_key('MagMax2')):
                
                amp = self.dicts[i]['MagMax1'] - self.dicts[i]['MagMin1']
                timedelta = abs(self.dicts[i]['MagMax1T'] - self.dicts[i]['MagMin1T'])
                timedelta /= self.streams[i][1].stats.sampling_rate
                #print self.dicts[i]['pazN']
                mag = estimateMagnitude(self.dicts[i]['pazN'], amp, timedelta,
                                        self.dicts[i]['distHypo'])
                amp = self.dicts[i]['MagMax2'] - self.dicts[i]['MagMin2']
                timedelta = abs(self.dicts[i]['MagMax2T'] - self.dicts[i]['MagMin2T'])
                timedelta /= self.streams[i][2].stats.sampling_rate
                mag += estimateMagnitude(self.dicts[i]['pazE'], amp, timedelta,
                                         self.dicts[i]['distHypo'])
                mag /= 2.
                self.dicts[i]['Mag'] = mag
                self.dicts[i]['MagChannel'] = '%s,%s' % (self.streams[i][1].stats.channel, self.streams[i][2].stats.channel)
                print 'calculated new magnitude for %s: %0.2f (channels: %s)' \
                      % (self.dicts[i]['Station'], self.dicts[i]['Mag'],
                         self.dicts[i]['MagChannel'])
            
            elif (self.dicts[i].has_key('MagMin1') and
                  self.dicts[i].has_key('MagMax1')):
                amp = self.dicts[i]['MagMax1'] - self.dicts[i]['MagMin1']
                timedelta = abs(self.dicts[i]['MagMax1T'] - self.dicts[i]['MagMin1T'])
                timedelta /= self.streams[i][1].stats.sampling_rate
                #print self.dicts[i]['pazN']
                mag = estimateMagnitude(self.dicts[i]['pazN'], amp, timedelta,
                                        self.dicts[i]['distHypo'])
                self.dicts[i]['Mag'] = mag
                self.dicts[i]['MagChannel'] = '%s' % self.streams[i][1].stats.channel
                print 'calculated new magnitude for %s: %0.2f (channels: %s)' \
                      % (self.dicts[i]['Station'], self.dicts[i]['Mag'],
                         self.dicts[i]['MagChannel'])
            
            elif (self.dicts[i].has_key('MagMin2') and
                  self.dicts[i].has_key('MagMax2')):
                amp = self.dicts[i]['MagMax2'] - self.dicts[i]['MagMin2']
                timedelta = abs(self.dicts[i]['MagMax2T'] - self.dicts[i]['MagMin2T'])
                timedelta /= self.streams[i][2].stats.sampling_rate
                #print self.dicts[i]['pazN']
                mag = estimateMagnitude(self.dicts[i]['pazE'], amp, timedelta,
                                        self.dicts[i]['distHypo'])
                self.dicts[i]['Mag'] = mag
                self.dicts[i]['MagChannel'] = '%s' % self.streams[i][2].stats.channel
                print 'calculated new magnitude for %s: %0.2f (channels: %s)' \
                      % (self.dicts[i]['Station'], self.dicts[i]['Mag'],
                         self.dicts[i]['MagChannel'])
                

    def showEventMap(self):
        #print self.dicts[0]
        self.figEventMap = plt.figure(2)
        self.axEventMap = self.figEventMap.add_subplot(111)
        self.axEventMap.scatter([self.EventLon], [self.EventLat],
                             30, color = 'red', marker = 'o')
        errLon, errLat = utlLonLat(self.EventLon, self.EventLat,
                               self.EventErrX, self.EventErrY)
        errLon -= self.EventLon
        errLat -= self.EventLat
        self.axEventMap.text(self.EventLon, self.EventLat,
                          ' %2.3f +/- %0.2fkm\n %2.3f +/- %0.2fkm\n %im +/- %im' % (self.EventLon,
                          self.EventErrX, self.EventLat,
                          self.EventErrY, self.EventZ * 1000,
                          self.EventErrZ * 1000), va = 'top',
                          family = 'monospace')
        self.netMagText = self.axEventMap.text(self.EventLon, self.EventLat,
                          self.netMagLabel,
                          va = 'top',
                          color = 'green',
                          family = 'monospace')
        errorell = Ellipse(xy = [self.EventLon, self.EventLat],
                      width = errLon, height = errLat, angle = 0, fill = False)
        self.axEventMap.add_artist(errorell)
        self.scatterMagIndices = []
        self.scatterMagLon = []
        self.scatterMagLat = []
        for i in range(len(self.streams)):
            # determine which stations are used in location
            if self.dicts[i].has_key('Pres') or self.dicts[i].has_key('Sres'):
                stationColor = 'black'
            else:
                stationColor = 'lightgray'
            # plot stations at respective coordinates with names
            self.axEventMap.scatter([self.dicts[i]['StaLon']],
                                    [self.dicts[i]['StaLat']], s = 150,
                                    marker = 'v', color = '',
                                    edgecolor = stationColor)
            self.axEventMap.text(self.dicts[i]['StaLon'],
                                 self.dicts[i]['StaLat'],
                                 '  ' + self.dicts[i]['Station'],
                                 color = stationColor,
                                 va = 'top', family = 'monospace')
            if self.dicts[i].has_key('Pres'):
                self.axEventMap.text(self.dicts[i]['StaLon'], self.dicts[i]['StaLat'],
                                  self.dicts[i]['PResInfo'], va = 'top',
                                  family = 'monospace',
                                  color = self.dictPhaseColors['P'])
            if self.dicts[i].has_key('Sres'):
                self.axEventMap.text(self.dicts[i]['StaLon'], self.dicts[i]['StaLat'],
                                  self.dicts[i]['SResInfo'], va = 'top',
                                  family = 'monospace',
                                  color = self.dictPhaseColors['S'])
            if self.dicts[i].has_key('Mag'):
                self.scatterMagIndices.append(i)
                self.scatterMagLon.append(self.dicts[i]['StaLon'])
                self.scatterMagLat.append(self.dicts[i]['StaLat'])
                self.axEventMap.text(self.dicts[i]['StaLon'], self.dicts[i]['StaLat'],
                                  '  ' + self.dicts[i]['Station'], va = 'top',
                                  family = 'monospace')
                self.axEventMap.text(self.dicts[i]['StaLon'], self.dicts[i]['StaLat'],
                                  '\n\n\n\n  %0.2f (%s)' % (self.dicts[i]['Mag'],
                                  self.dicts[i]['MagChannel']), va = 'top',
                                  family = 'monospace',
                                  color = self.dictPhaseColors['Mag'])
            if len(self.scatterMagLon) > 0 :
                self.scatterMag = self.axEventMap.scatter(self.scatterMagLon, self.scatterMagLat, s = 150,
                                     marker = 'v', color = '', edgecolor = 'black', picker = 10)
                
        self.axEventMap.set_xlabel('Longitude')
        self.axEventMap.set_ylabel('Latitude')
        self.axEventMap.set_title(self.EventTime)
        #XXX disabled because it plots the wrong info if the event was
        # fetched from seishub
        #####lines = open(self.threeDlocOutfile).readlines()
        #####infoEvent = lines[0].rstrip()
        #####infoPicks = ''
        #####for line in lines[1:]:
        #####    infoPicks += line
        #####self.axEventMap.text(0.02, 0.95, infoEvent, transform = self.axEventMap.transAxes,
        #####                  fontsize = 12, verticalalignment = 'top',
        #####                  family = 'monospace')
        #####self.axEventMap.text(0.02, 0.90, infoPicks, transform = self.axEventMap.transAxes,
        #####                  fontsize = 10, verticalalignment = 'top',
        #####                  family = 'monospace')
        self.figEventMap.canvas.mpl_connect('pick_event', self.selectMagnitudes)
        try:
            self.scatterMag.set_facecolors(self.eventMapColors)
        except:
            pass
        plt.show()

    def selectMagnitudes(self, event):
        if event.artist != self.scatterMag:
            return
        i = self.scatterMagIndices[event.ind[0]]
        j = event.ind[0]
        self.dicts[i]['MagUse'] = not self.dicts[i]['MagUse']
        #print event.ind[0]
        #print i
        #print event.artist
        #for di in self.dicts:
        #    print di['MagUse']
        #print i
        #print self.dicts[i]['MagUse']
        if self.dicts[i]['MagUse']:
            self.eventMapColors[j] = (0.,  1.,  0.,  1.)
        else:
            self.eventMapColors[j] = (0.,  0.,  0.,  0.)
        #print self.eventMapColors
        self.scatterMag.set_facecolors(self.eventMapColors)
        #print self.scatterMag.get_facecolors()
        #event.artist.set_facecolors(self.eventMapColors)
        self.updateNetworkMag()
        self.figEventMap.canvas.draw()

    def picks2XML(self):
        """
        Returns output of picks as xml file
        """
        xml =  Element("event")
        Sub(Sub(xml, "event_id"), "value").text = self.xmlEventID
        Sub(Sub(xml, "event_type"), "value").text = "manual"
        
        # we save P picks on Z-component and S picks on N-component
        # XXX standard values for unset keys!!!???!!!???
        epidists = []
        for i in range(len(self.streams)):
            if self.dicts[i].has_key('P'):
                pick = Sub(xml, "pick")
                wave = Sub(pick, "waveform")
                wave.set("networkCode", self.streams[i][0].stats.network) 
                wave.set("stationCode", self.streams[i][0].stats.station) 
                wave.set("channelCode", self.streams[i][0].stats.channel) 
                wave.set("locationCode", "") 
                date = Sub(pick, "time")
                # prepare time of pick
                picktime = self.streams[i][0].stats.starttime
                picktime += (self.dicts[i]['P'] /
                             self.streams[i][0].stats.sampling_rate)
                Sub(date, "value").text = picktime.isoformat() # + '.%06i' % picktime.microsecond)
                if self.dicts[i].has_key('PErr1') and self.dicts[i].has_key('PErr2'):
                    temp = float(self.dicts[i]['PErr2'] -
                                 self.dicts[i]['PErr1'])
                    temp /= self.streams[i][0].stats.sampling_rate
                    Sub(date, "uncertainty").text = str(temp)
                else:
                    Sub(date, "uncertainty")
                Sub(pick, "phaseHint").text = "P"
                if self.dicts[i].has_key('POnset'):
                    Sub(pick, "onset").text = self.dicts[i]['POnset']
                else:
                    Sub(pick, "onset")
                if self.dicts[i].has_key('PPol'):
                    if self.dicts[i]['PPol'] == 'up' or self.dicts[i]['PPol'] == 'poorup':
                        Sub(pick, "polarity").text = 'positive'
                    elif self.dicts[i]['PPol'] == 'down' or self.dicts[i]['PPol'] == 'poordown':
                        Sub(pick, "polarity").text = 'negative'
                else:
                    Sub(pick, "polarity")
                if self.dicts[i].has_key('PWeight'):
                    Sub(pick, "weight").text = '%i' % self.dicts[i]['PWeight']
                else:
                    Sub(pick, "weight")
                Sub(Sub(pick, "min_amp"), "value") #XXX what is min_amp???
        
            if self.dicts[i].has_key('S'):
                axind = self.dicts[i]['Saxind']
                pick = Sub(xml, "pick")
                wave = Sub(pick, "waveform")
                wave.set("networkCode", self.streams[i][axind].stats.network) 
                wave.set("stationCode", self.streams[i][axind].stats.station) 
                wave.set("channelCode", self.streams[i][axind].stats.channel) 
                wave.set("locationCode", "") 
                date = Sub(pick, "time")
                # prepare time of pick
                picktime = self.streams[i][axind].stats.starttime
                picktime += (self.dicts[i]['S'] /
                             self.streams[i][axind].stats.sampling_rate)
                Sub(date, "value").text = picktime.isoformat() # + '.%06i' % picktime.microsecond)
                if self.dicts[i].has_key('SErr1') and self.dicts[i].has_key('SErr2'):
                    temp = float(self.dicts[i]['SErr2'] -
                                 self.dicts[i]['SErr1'])
                    temp /= self.streams[i][axind].stats.sampling_rate
                    Sub(date, "uncertainty").text = str(temp)
                else:
                    Sub(date, "uncertainty")
                Sub(pick, "phaseHint").text = "S"
                if self.dicts[i].has_key('SOnset'):
                    Sub(pick, "onset").text = self.dicts[i]['SOnset']
                else:
                    Sub(pick, "onset")
                if self.dicts[i].has_key('SPol'):
                    if self.dicts[i]['SPol'] == 'up' or self.dicts[i]['SPol'] == 'poorup':
                        Sub(pick, "polarity").text = 'positive'
                    elif self.dicts[i]['SPol'] == 'down' or self.dicts[i]['SPol'] == 'poordown':
                        Sub(pick, "polarity").text = 'negative'
                else:
                    Sub(pick, "polarity")
                if self.dicts[i].has_key('SWeight'):
                    Sub(pick, "weight").text = '%i' % self.dicts[i]['SWeight']
                else:
                    Sub(pick, "weight")
                Sub(Sub(pick, "min_amp"), "value") #XXX what is min_amp???
        return tostring(xml,pretty_print=True,xml_declaration=True)
    
    #XXX still have to adjust this to jo's hypo2000 xml look and feel
    def hyp20002XML(self):
        """
        Returns output of hypo2000 as xml file
        """
        xml =  Element("event")
        Sub(Sub(xml, "event_id"), "value").text = self.xmlEventID
        Sub(Sub(xml, "event_type"), "value").text = "manual"
        
        # we save P picks on Z-component and S picks on N-component
        # XXX standard values for unset keys!!!???!!!???
        epidists = []
        for i in range(len(self.streams)):
            if self.dicts[i].has_key('P'):
                pick = Sub(xml, "pick")
                wave = Sub(pick, "waveform")
                wave.set("networkCode", self.streams[i][0].stats.network) 
                wave.set("stationCode", self.streams[i][0].stats.station) 
                wave.set("channelCode", self.streams[i][0].stats.channel) 
                wave.set("locationCode", "") 
                date = Sub(pick, "time")
                # prepare time of pick
                picktime = self.streams[i][0].stats.starttime
                picktime += (self.dicts[i]['P'] /
                             self.streams[i][0].stats.sampling_rate)
                Sub(date, "value").text = picktime.isoformat() # + '.%06i' % picktime.microsecond)
                if self.dicts[i].has_key('PErr1') and self.dicts[i].has_key('PErr2'):
                    temp = float(self.dicts[i]['PErr2'] -
                                 self.dicts[i]['PErr1'])
                    temp /= self.streams[i][0].stats.sampling_rate
                    Sub(date, "uncertainty").text = str(temp)
                else:
                    Sub(date, "uncertainty")
                Sub(pick, "phaseHint").text = "P"
                if self.dicts[i].has_key('POnset'):
                    Sub(pick, "onset").text = self.dicts[i]['POnset']
                else:
                    Sub(pick, "onset")
                if self.dicts[i].has_key('PPol'):
                    if self.dicts[i]['PPol'] == 'up' or self.dicts[i]['PPol'] == 'poorup':
                        Sub(pick, "polarity").text = 'positive'
                    elif self.dicts[i]['PPol'] == 'down' or self.dicts[i]['PPol'] == 'poordown':
                        Sub(pick, "polarity").text = 'negative'
                else:
                    Sub(pick, "polarity")
                if self.dicts[i].has_key('PWeight'):
                    Sub(pick, "weight").text = '%i' % self.dicts[i]['PWeight']
                else:
                    Sub(pick, "weight")
                Sub(Sub(pick, "min_amp"), "value") #XXX what is min_amp???
                
                if self.dicts[i].has_key('Psynth'):
                    Sub(pick, "phase_compu").text #XXX this is redundant. can be constructed from above info
                    Sub(Sub(pick, "phase_res"), "value").text = '%s' % self.dicts[i]['Pres']
                    Sub(Sub(pick, "phase_weight"), "value").text = '%s' % self.dicts[i]['PsynthWeight']
                    Sub(Sub(pick, "phase_delay"), "value")
                    Sub(Sub(pick, "azimuth"), "value").text = '%s' % self.dicts[i]['PAzim']
                    Sub(Sub(pick, "incident"), "value").text = '%s' % self.dicts[i]['PInci']
                    Sub(Sub(pick, "epi_dist"), "value").text = '%s' % self.dicts[i]['distEpi']
                    Sub(Sub(pick, "hyp_dist"), "value").text = '%s' % self.dicts[i]['distHypo']
        
            if self.dicts[i].has_key('S'):
                axind = self.dicts[i]['Saxind']
                pick = Sub(xml, "pick")
                wave = Sub(pick, "waveform")
                wave.set("networkCode", self.streams[i][axind].stats.network) 
                wave.set("stationCode", self.streams[i][axind].stats.station) 
                wave.set("channelCode", self.streams[i][axind].stats.channel) 
                wave.set("locationCode", "") 
                date = Sub(pick, "time")
                # prepare time of pick
                picktime = self.streams[i][axind].stats.starttime
                picktime += (self.dicts[i]['S'] /
                             self.streams[i][axind].stats.sampling_rate)
                Sub(date, "value").text = picktime.isoformat() # + '.%06i' % picktime.microsecond)
                if self.dicts[i].has_key('SErr1') and self.dicts[i].has_key('SErr2'):
                    temp = float(self.dicts[i]['SErr2'] -
                                 self.dicts[i]['SErr1'])
                    temp /= self.streams[i][axind].stats.sampling_rate
                    Sub(date, "uncertainty").text = str(temp)
                else:
                    Sub(date, "uncertainty")
                Sub(pick, "phaseHint").text = "S"
                if self.dicts[i].has_key('SOnset'):
                    Sub(pick, "onset").text = self.dicts[i]['SOnset']
                else:
                    Sub(pick, "onset")
                if self.dicts[i].has_key('SPol'):
                    if self.dicts[i]['SPol'] == 'up' or self.dicts[i]['SPol'] == 'poorup':
                        Sub(pick, "polarity").text = 'positive'
                    elif self.dicts[i]['SPol'] == 'down' or self.dicts[i]['SPol'] == 'poordown':
                        Sub(pick, "polarity").text = 'negative'
                else:
                    Sub(pick, "polarity")
                if self.dicts[i].has_key('SWeight'):
                    Sub(pick, "weight").text = '%i' % self.dicts[i]['SWeight']
                else:
                    Sub(pick, "weight")
                Sub(Sub(pick, "min_amp"), "value") #XXX what is min_amp???
                
                if self.dicts[i].has_key('Ssynth'):
                    Sub(pick, "phase_compu").text #XXX this is redundant. can be constructed from above info
                    Sub(Sub(pick, "phase_res"), "value").text = '%s' % self.dicts[i]['Sres']
                    Sub(Sub(pick, "phase_weight"), "value").text = '%s' % self.dicts[i]['SsynthWeight']
                    Sub(Sub(pick, "phase_delay"), "value")
                    Sub(Sub(pick, "azimuth"), "value").text = '%s' % self.dicts[i]['SAzim']
                    Sub(Sub(pick, "incident"), "value").text = '%s' % self.dicts[i]['SInci']
                    Sub(Sub(pick, "epi_dist"), "value").text = '%s' % self.dicts[i]['distEpi']
                    Sub(Sub(pick, "hyp_dist"), "value").text = '%s' % self.dicts[i]['distHypo']
        
        #XXX XXX XXX XXX check lines below and compare to e.g.
        # teide:8080/xml/seismology/event/baynet09_0641.xml
        # especially "earth_mod" could be set with meaningful value
        # read from hypo2000 output
        origin = Sub(xml, "origin")
        date = Sub(origin, "time")
        Sub(date, "value").text = self.EventTime.isoformat() # + '.%03i' % self.EventTime.microsecond
        Sub(date, "uncertainty")
        lat = Sub(origin, "latitude")
        Sub(lat, "value").text = '%s' % self.EventLat
        Sub(lat, "uncertainty").text = '%s' % self.EventErrY #XXX Lat Error in km??!!
        lon = Sub(origin, "longitude")
        Sub(lon, "value").text = '%s' % self.EventLon
        Sub(lon, "uncertainty").text = '%s' % self.EventErrX #XXX Lon Error in km??!!
        depth = Sub(origin, "depth")
        Sub(depth, "value").text = '%s' % self.EventZ
        Sub(depth, "uncertainty").text = '%s' % self.EventErrZ
        Sub(origin, "depth_type").text = "from location program"
        Sub(origin, "earth_mod").text = "STAUFEN"
        Sub(origin, "originUncertainty")
        quality = Sub(origin, "originQuality")
        Sub(quality, "P_usedPhaseCount").text = '%i' % self.PCount
        Sub(quality, "S_usedPhaseCount").text = '%i' % self.SCount
        Sub(quality, "usedPhaseCount").text = '%i' % (self.PCount + self.SCount)
        Sub(quality, "usedStationCount").text = '%i' % self.usedStationsCount
        Sub(quality, "associatedPhaseCount").text = '%i' % (self.PCount + self.SCount)
        Sub(quality, "associatedStationCount").text = '%i' % len(self.dicts)
        Sub(quality, "depthPhaseCount").text = "0"
        Sub(quality, "standardError").text = '%s' % self.EventStdErr
        Sub(quality, "secondaryAzimuthalGap").text = '%s' % self.EventAzimGap
        Sub(quality, "groundTruthLevel")
        Sub(quality, "minimumDistance").text = '%s' % self.epidistMin
        Sub(quality, "maximumDistance").text = '%s' % self.epidistMax
        Sub(quality, "medianDistance").text = '%s' % self.epidistMedian
        magnitude = Sub(xml, "magnitude")
        mag = Sub(magnitude, "mag")
        if np.isnan(self.netMag):
            Sub(mag, "value")
            Sub(mag, "uncertainty")
        else:
            Sub(mag, "value").text = '%s' % self.netMag
            Sub(mag, "uncertainty").text = '%s' % self.netMagVar
        Sub(magnitude, "type").text = "Ml"
        Sub(magnitude, "stationCount").text = '%i' % self.staMagCount
        for i in range(len(self.streams)):
            stationMagnitude = Sub(xml, "stationMagnitude")
            if self.dicts[i].has_key('Mag'):
                mag = Sub(stationMagnitude, 'mag')
                Sub(mag, 'value').text = '%s' % self.dicts[i]['Mag']
                Sub(mag, 'uncertainty').text
                Sub(stationMagnitude, 'station').text = '%s' % self.dicts[i]['Station']
                if self.dicts[i]['MagUse']:
                    Sub(stationMagnitude, 'weight').text = '%s' % (1. / self.staMagCount)
                else:
                    Sub(stationMagnitude, 'weight').text = '0'
                Sub(stationMagnitude, 'channels').text = '%s' % self.dicts[i]['MagChannel']
        return tostring(xml,pretty_print=True,xml_declaration=True)

    def threeDLoc2XML(self):
        """
        Returns output of 3dloc as xml file
        """
        xml =  Element("event")
        Sub(Sub(xml, "event_id"), "value").text = self.xmlEventID
        Sub(Sub(xml, "event_type"), "value").text = "manual"
        
        # we save P picks on Z-component and S picks on N-component
        # XXX standard values for unset keys!!!???!!!???
        epidists = []
        for i in range(len(self.streams)):
            if self.dicts[i].has_key('P'):
                pick = Sub(xml, "pick")
                wave = Sub(pick, "waveform")
                wave.set("networkCode", self.streams[i][0].stats.network) 
                wave.set("stationCode", self.streams[i][0].stats.station) 
                wave.set("channelCode", self.streams[i][0].stats.channel) 
                wave.set("locationCode", "") 
                date = Sub(pick, "time")
                # prepare time of pick
                picktime = self.streams[i][0].stats.starttime
                picktime += (self.dicts[i]['P'] /
                             self.streams[i][0].stats.sampling_rate)
                Sub(date, "value").text = picktime.isoformat() # + '.%06i' % picktime.microsecond)
                if self.dicts[i].has_key('PErr1') and self.dicts[i].has_key('PErr2'):
                    temp = float(self.dicts[i]['PErr2'] -
                                 self.dicts[i]['PErr1'])
                    temp /= self.streams[i][0].stats.sampling_rate
                    Sub(date, "uncertainty").text = str(temp)
                else:
                    Sub(date, "uncertainty")
                Sub(pick, "phaseHint").text = "P"
                if self.dicts[i].has_key('POnset'):
                    Sub(pick, "onset").text = self.dicts[i]['POnset']
                else:
                    Sub(pick, "onset")
                if self.dicts[i].has_key('PPol'):
                    if self.dicts[i]['PPol'] == 'up' or self.dicts[i]['PPol'] == 'poorup':
                        Sub(pick, "polarity").text = 'positive'
                    elif self.dicts[i]['PPol'] == 'down' or self.dicts[i]['PPol'] == 'poordown':
                        Sub(pick, "polarity").text = 'negative'
                else:
                    Sub(pick, "polarity")
                if self.dicts[i].has_key('PWeight'):
                    Sub(pick, "weight").text = '%i' % self.dicts[i]['PWeight']
                else:
                    Sub(pick, "weight")
                Sub(Sub(pick, "min_amp"), "value") #XXX what is min_amp???
                
                if self.dicts[i].has_key('Psynth'):
                    Sub(pick, "phase_compu").text #XXX this is redundant. can be constructed from above info
                    Sub(Sub(pick, "phase_res"), "value").text = '%s' % self.dicts[i]['Pres']
                    Sub(Sub(pick, "phase_weight"), "value") #wird von hypoXX ausgespuckt
                    Sub(Sub(pick, "phase_delay"), "value")
                    Sub(Sub(pick, "azimuth"), "value").text = '%s' % self.dicts[i]['PAzim']
                    Sub(Sub(pick, "incident"), "value").text = '%s' % self.dicts[i]['PInci']
                    Sub(Sub(pick, "epi_dist"), "value").text = '%s' % self.dicts[i]['distEpi']
                    Sub(Sub(pick, "hyp_dist"), "value").text = '%s' % self.dicts[i]['distHypo']
        
            if self.dicts[i].has_key('S'):
                axind = self.dicts[i]['Saxind']
                pick = Sub(xml, "pick")
                wave = Sub(pick, "waveform")
                wave.set("networkCode", self.streams[i][axind].stats.network) 
                wave.set("stationCode", self.streams[i][axind].stats.station) 
                wave.set("channelCode", self.streams[i][axind].stats.channel) 
                wave.set("locationCode", "") 
                date = Sub(pick, "time")
                # prepare time of pick
                picktime = self.streams[i][axind].stats.starttime
                picktime += (self.dicts[i]['S'] /
                             self.streams[i][axind].stats.sampling_rate)
                Sub(date, "value").text = picktime.isoformat() # + '.%06i' % picktime.microsecond)
                if self.dicts[i].has_key('SErr1') and self.dicts[i].has_key('SErr2'):
                    temp = float(self.dicts[i]['SErr2'] -
                                 self.dicts[i]['SErr1'])
                    temp /= self.streams[i][axind].stats.sampling_rate
                    Sub(date, "uncertainty").text = str(temp)
                else:
                    Sub(date, "uncertainty")
                Sub(pick, "phaseHint").text = "S"
                if self.dicts[i].has_key('SOnset'):
                    Sub(pick, "onset").text = self.dicts[i]['SOnset']
                else:
                    Sub(pick, "onset")
                if self.dicts[i].has_key('SPol'):
                    if self.dicts[i]['SPol'] == 'up' or self.dicts[i]['SPol'] == 'poorup':
                        Sub(pick, "polarity").text = 'positive'
                    elif self.dicts[i]['SPol'] == 'down' or self.dicts[i]['SPol'] == 'poordown':
                        Sub(pick, "polarity").text = 'negative'
                else:
                    Sub(pick, "polarity")
                if self.dicts[i].has_key('SWeight'):
                    Sub(pick, "weight").text = '%i' % self.dicts[i]['SWeight']
                else:
                    Sub(pick, "weight")
                Sub(Sub(pick, "min_amp"), "value") #XXX what is min_amp???
                
                if self.dicts[i].has_key('Ssynth'):
                    Sub(pick, "phase_compu").text = #XXX this is redundant. can be constructed from above info
                    Sub(Sub(pick, "phase_res"), "value").text = '%s' % self.dicts[i]['Sres']
                    Sub(Sub(pick, "phase_weight"), "value") #wird von hypoXX ausgespuckt
                    Sub(Sub(pick, "phase_delay"), "value")
                    Sub(Sub(pick, "azimuth"), "value").text = '%s' % self.dicts[i]['SAzim']
                    Sub(Sub(pick, "incident"), "value").text = '%s' % self.dicts[i]['SInci']
                    Sub(Sub(pick, "epi_dist"), "value").text = '%s' % self.dicts[i]['distEpi']
                    Sub(Sub(pick, "hyp_dist"), "value").text = '%s' % self.dicts[i]['distHypo']
        
        origin = Sub(xml, "origin")
        date = Sub(origin, "time")
        Sub(date, "value").text = self.EventTime.isoformat() # + '.%03i' % self.EventTime.microsecond
        Sub(date, "uncertainty")
        lat = Sub(origin, "latitude")
        Sub(lat, "value").text = '%s' % self.EventLat
        Sub(lat, "uncertainty").text = '%s' % self.EventErrY #XXX Lat Error in km??!!
        lon = Sub(origin, "longitude")
        Sub(lon, "value").text = '%s' % self.EventLon
        Sub(lon, "uncertainty").text = '%s' % self.EventErrX #XXX Lon Error in km??!!
        depth = Sub(origin, "depth")
        Sub(depth, "value").text = '%s' % self.EventZ
        Sub(depth, "uncertainty").text = '%s' % self.EventErrZ
        Sub(origin, "depth_type").text = "from location program"
        Sub(origin, "earth_mod").text = "STAUFEN"
        Sub(origin, "originUncertainty")
        quality = Sub(origin, "originQuality")
        Sub(quality, "P_usedPhaseCount").text = '%i' % self.PCount
        Sub(quality, "S_usedPhaseCount").text = '%i' % self.SCount
        Sub(quality, "usedPhaseCount").text = '%i' % (self.PCount + self.SCount)
        Sub(quality, "usedStationCount").text = '%i' % self.usedStationsCount
        Sub(quality, "associatedPhaseCount").text = '%i' % (self.PCount + self.SCount)
        Sub(quality, "associatedStationCount").text = '%i' % len(self.dicts)
        Sub(quality, "depthPhaseCount").text = "0"
        Sub(quality, "standardError").text = '%s' % self.EventStdErr
        Sub(quality, "secondaryAzimuthalGap").text = '%s' % self.EventAzimGap
        Sub(quality, "groundTruthLevel")
        Sub(quality, "minimumDistance").text = '%s' % self.epidistMin
        Sub(quality, "maximumDistance").text = '%s' % self.epidistMax
        Sub(quality, "medianDistance").text = '%s' % self.epidistMedian
        magnitude = Sub(xml, "magnitude")
        mag = Sub(magnitude, "mag")
        if np.isnan(self.netMag):
            Sub(mag, "value")
            Sub(mag, "uncertainty")
        else:
            Sub(mag, "value").text = '%s' % self.netMag
            Sub(mag, "uncertainty").text = '%s' % self.netMagVar
        Sub(magnitude, "type").text = "Ml"
        Sub(magnitude, "stationCount").text = '%i' % self.staMagCount
        for i in range(len(self.streams)):
            stationMagnitude = Sub(xml, "stationMagnitude")
            if self.dicts[i].has_key('Mag'):
                mag = Sub(stationMagnitude, 'mag')
                Sub(mag, 'value').text = '%s' % self.dicts[i]['Mag']
                Sub(mag, 'uncertainty').text
                Sub(stationMagnitude, 'station').text = '%s' % self.dicts[i]['Station']
                if self.dicts[i]['MagUse']:
                    Sub(stationMagnitude, 'weight').text = '%s' % (1. / self.staMagCount)
                else:
                    Sub(stationMagnitude, 'weight').text = '0'
                Sub(stationMagnitude, 'channels').text = '%s' % self.dicts[i]['MagChannel']
        return tostring(xml,pretty_print=True,xml_declaration=True)


    def uploadSeishub(self):
        """
        Upload xml file to seishub
        """
        userid = "admin"
        passwd = "admin"

        auth = 'Basic ' + (base64.encodestring(userid + ':' + passwd)).strip()

        servername = 'teide:8080'
        path = '/xml/seismology/event'
        
        # determine which location was run and how the xml should be created
        if self.locationType = "3dloc":
            data = self.threeDLoc2XML()
        elif self.locationType = "hyp2000":
            data = self.hyp20002XML()
        #XXX there should be a method that creates a xml with pick info only
        else:
            data = self.picks2XML()
        #XXX remove later
        self.xmlEventID = '%i' % 1265906465.2780671
        name = "obspyck_%s" % (self.xmlEventID) #XXX id of the file

        #construct and send the header
        webservice = httplib.HTTP(servername)
        webservice.putrequest("PUT", path + '/' + name)
        webservice.putheader('Authorization', auth )
        webservice.putheader("Host", "localhost")
        webservice.putheader("User-Agent", "obspyck")
        webservice.putheader("Content-type", "text/xml; charset=\"UTF-8\"")
        webservice.putheader("Content-length", "%d" % len(data))
        webservice.endheaders()
        webservice.send(data)

        # get the response
        statuscode, statusmessage, header = webservice.getreply()
        if statuscode!=201:
           print "Server: ", servername, path
           print "Response: ", statuscode, statusmessage
           print "Headers: ", header
        else:
            print 'Upload to seishub successful (EventId: %s)' % name
    
    def clearDictionaries(self):
        print "Clearing previous data."
        for i in range(len(self.dicts)):
            for k in self.dicts[i].keys():
                if k != 'Station' and k != 'StaLat' and k != 'StaLon' and \
                   k != 'StaEle' and k != 'pazZ' and k != 'pazN' and \
                   k != 'pazE':
                    del self.dicts[i][k]
            self.dicts[i]['MagUse'] = True

    def clearEventDictionaries(self):
        print "Clearing previous event data."
        for i in range(len(self.dicts)):
            for k in self.dicts[i].keys():
                if k != 'Station' and k != 'StaLat' and k != 'StaLon' and \
                   k != 'StaEle' and k != 'pazZ' and k != 'pazN' and \
                   k != 'pazE' and k != 'P' and k != 'PErr1' and \
                   k != 'PErr2' and k != 'POnset' and k != 'PPol' and \
                   k != 'PWeight' and k != 'S' and k != 'SErr1' and \
                   k != 'SErr2' and k != 'SOnset' and k != 'SPol' and \
                   k != 'SWeight' and k != 'Saxind':
                    del self.dicts[i][k]
            self.dicts[i]['MagUse'] = True

    def delAllItems(self):
        self.delPLine()
        self.delPErr1Line()
        self.delPErr2Line()
        self.delPLabel()
        self.delPsynthLine()
        self.delPsynthLabel()
        self.delSLine()
        self.delSErr1Line()
        self.delSErr2Line()
        self.delSLabel()
        self.delSsynthLine()
        self.delSsynthLabel()
        self.delMagMaxCross1()
        self.delMagMinCross1()
        self.delMagMaxCross2()
        self.delMagMinCross2()

    def drawAllItems(self):
        self.drawPLine()
        self.drawPErr1Line()
        self.drawPErr2Line()
        self.drawPLabel()
        self.drawPsynthLine()
        self.drawPsynthLabel()
        self.drawSLine()
        self.drawSErr1Line()
        self.drawSErr2Line()
        self.drawSLabel()
        self.drawSsynthLine()
        self.drawSsynthLabel()
        self.drawMagMaxCross1()
        self.drawMagMinCross1()
        self.drawMagMaxCross2()
        self.drawMagMinCross2()

    def getNextEventFromSeishub(self, starttime, endtime):
        """
        Updates dictionary with pick data for first event which origin time
        is between startime and endtime.
        Warning:
         * When using the stream starttime an interesting event may not be
           found because the origin time may be before the stream starttime!
         * If more than one event is found in given time range only the first
           one is used, all others are disregarded!

        :param starttime: Start datetime as UTCDateTime
        :param endtime: End datetime as UTCDateTime
        """

        url = "http://teide:8080/seismology/event/getList?" + \
              "min_datetime=%s&max_datetime=%s" % \
              (str(starttime), str(endtime))
        req = urllib2.Request(url)
        auth = base64.encodestring('%s:%s' % ("admin", "admin"))[:-1]
        req.add_header("Authorization", "Basic %s" % auth)

        f = urllib2.urlopen(req)
        xml = parse(f)
        f.close()

        picklist = []

        # iterate the counter that indicates which event to fetch
        if not self.seishubEventCount:
            self.seishubEventCount = len(xml.xpath(u".//resource_name"))
            self.seishubEventCurrent = 0
            print "%i events are available from Seishub" % self.seishubEventCount
        else:
            self.seishubEventCurrent = (self.seishubEventCurrent + 1) % \
                                       self.seishubEventCount

        # define which event data we will fetch
        node = xml.xpath(u".//resource_name")[self.seishubEventCurrent]
        #document = xml.xpath(".//document_id")
        #document_id = document[self.seishubEventCurrent].text
        # Hack to show xml resource as document id
        document_id = node.text
        
        print "Fetching event %i of %i (event_id: %s)" %  \
              (self.seishubEventCurrent + 1, self.seishubEventCount,
               document_id)
        resource_url = "http://teide:8080/xml/seismology/event/" + \
                       node.text
        resource_req = urllib2.Request(resource_url)
        resource_req.add_header("Authorization", "Basic %s" % auth)
        fp = urllib2.urlopen(resource_req)
        resource_xml = parse(fp)
        fp.close()
        #analyze picks:
        for pick in resource_xml.xpath(u".//pick"):
            # attributes
            id = pick.find("waveform").attrib
            network = id["networkCode"]
            station = id["stationCode"]
            location = id["locationCode"]
            channel = id['channelCode']
            streamnum = None
            # search for streamnumber corresponding to pick
            for i in range(len(self.streams)):
                if station.strip() != self.dicts[i]['Station']:
                    continue
                else:
                    streamnum = i
                    break
            if streamnum == None:
                message = "Did not find matching stream for pick data " + \
                          "with station id: \"%s\"" % station.strip()
                warnings.warn(message)
                continue
            # values
            time = pick.xpath(".//time/value")[0].text
            uncertainty = pick.xpath(".//time/uncertainty")[0].text
            try:
                onset = pick.xpath(".//onset")[0].text
            except:
                onset = None
            try:
                polarity = pick.xpath(".//polarity")[0].text
            except:
                polarity = None
            try:
                weight = pick.xpath(".//weight")[0].text
            except:
                weight = None
            try:
                phase_res = pick.xpath(".//phase_res/value")[0].text
            except:
                phase_res = None
            try:
                phase_weight = pick.xpath(".//phase_res/weight")[0].text
            except:
                phase_weight = None
            try:
                azimuth = pick.xpath(".//azimuth/value")[0].text
            except:
                azimuth = None
            try:
                incident = pick.xpath(".//incident/value")[0].text
            except:
                incident = None
            try:
                epi_dist = pick.xpath(".//epi_dist/value")[0].text
            except:
                epi_dist = None
            try:
                hyp_dist = pick.xpath(".//hyp_dist/value")[0].text
            except:
                hyp_dist = None
            # convert UTC time to samples after stream starttime
            time = UTCDateTime(time)
            time -= self.streams[streamnum][0].stats.starttime
            time = int(round(time *
                     self.streams[streamnum][0].stats.sampling_rate))
            if phase_res:
                phase_res_samps = float(phase_res)
                phase_res_samps *= self.streams[streamnum][0].stats.sampling_rate
                phase_res_samps = int(round(phase_res_samps))
            # map uncertainty in seconds to error picks in samples
            if uncertainty:
                uncertainty = float(uncertainty)
                uncertainty = int(round(uncertainty * \
                        self.streams[streamnum][0].stats.sampling_rate))
                uncertainty /= 2
            # assign to dictionary
            if pick.xpath(".//phaseHint")[0].text == "P":
                self.dicts[streamnum]['P'] = time
                if uncertainty:
                    self.dicts[streamnum]['PErr1'] = time - uncertainty
                    self.dicts[streamnum]['PErr2'] = time + uncertainty
                if onset:
                    self.dicts[streamnum]['POnset'] = onset
                if polarity:
                    self.dicts[streamnum]['PPol'] = polarity
                if weight:
                    self.dicts[streamnum]['PWeight'] = weight
                if phase_res:
                    self.dicts[streamnum]['Psynth'] = time + phase_res_samps
                    self.dicts[streamnum]['Pres'] = float(phase_res)
                if phase_weight:
                    self.dicts[streamnum]['PsynthWeight'] = phase_weight
                if azimuth:
                    self.dicts[streamnum]['PAzim'] = float(azimuth)
                if incident:
                    self.dicts[streamnum]['PInci'] = float(incident)
            if pick.xpath(".//phaseHint")[0].text == "S":
                self.dicts[streamnum]['S'] = time
                # XXX maybe dangerous to check last character:
                if channel.endswith('N'):
                    self.dicts[streamnum]['Saxind'] = 1
                if channel.endswith('E'):
                    self.dicts[streamnum]['Saxind'] = 2
                if uncertainty:
                    self.dicts[streamnum]['SErr1'] = time - uncertainty
                    self.dicts[streamnum]['SErr2'] = time + uncertainty
                if onset:
                    self.dicts[streamnum]['SOnset'] = onset
                if polarity:
                    self.dicts[streamnum]['SPol'] = polarity
                if weight:
                    self.dicts[streamnum]['SWeight'] = weight
                if phase_res:
                    self.dicts[streamnum]['Ssynth'] = time + phase_res_samps
                    self.dicts[streamnum]['Sres'] = float(phase_res)
                if phase_weight:
                    self.dicts[streamnum]['SsynthWeight'] = phase_weight
                if azimuth:
                    self.dicts[streamnum]['SAzim'] = float(azimuth)
                if incident:
                    self.dicts[streamnum]['SInci'] = float(incident)
            if epi_dist:
                self.dicts[streamnum]['distEpi'] = float(epi_dist)
            if hyp_dist:
                self.dicts[streamnum]['distHypo'] = float(hyp_dist)

        #analyze origin:
        origin = resource_xml.xpath(u".//origin")[0]
        try:
            time = origin.xpath(".//time/value")[0].text
            self.EventTime = UTCDateTime(time)
        except:
            pass
        try:
            lat = origin.xpath(".//latitude/value")[0].text
            self.EventLat = float(lat)
        except:
            pass
        try:
            lon = origin.xpath(".//longitude/value")[0].text
            self.EventLon = float(lon)
        except:
            pass
        try:
            errX = origin.xpath(".//longitude/uncertainty")[0].text
            self.EventErrX = float(errX)
        except:
            pass
        try:
            errY = origin.xpath(".//latitude/uncertainty")[0].text
            self.EventErrY = float(errY)
        except:
            pass
        try:
            z = origin.xpath(".//depth/value")[0].text
            self.EventZ = float(z)
        except:
            pass
        try:
            errZ = origin.xpath(".//depth/uncertainty")[0].text
            self.EventErrZ = float(errZ)
        except:
            pass
        try:
            self.PCount = \
                    int(origin.xpath(".//originQuality/P_usedPhaseCount")[0].text)
        except:
            pass
        try:
            self.SCount = \
                    int(origin.xpath(".//originQuality/S_usedPhaseCount")[0].text)
        except:
            pass
        try:
            self.usedStationsCount = \
                    int(origin.xpath(".//originQuality/usedStationCount")[0].text)
        except:
            pass
        try:
            self.EventStdErr = \
                    float(origin.xpath(".//originQuality/standardError")[0].text)
        except:
            pass
        try:
            self.EventAzimGap = \
                    float(origin.xpath(".//originQuality/secondaryAzimuthalGap")[0].text)
        except:
            pass
        try:
            self.epidistMin = \
                    float(origin.xpath(".//originQuality/minimumDistance")[0].text)
        except:
            pass
        try:
            self.epidistMax = \
                    float(origin.xpath(".//originQuality/maximumDistance")[0].text)
        except:
            pass
        try:
            self.epidistMedian = \
                    float(origin.xpath(".//originQuality/medianDistance")[0].text)
        except:
            pass

        #analyze magnitude:
        magnitude = resource_xml.xpath(u".//magnitude")[0]
        try:
            mag = magnitude.xpath(".//mag/value")[0].text
            self.netMag = float(mag)
            self.netMagLabel = '\n\n\n\n  %.2f (Var: %.2f)' % (self.netMag, self.netMagVar)
        except:
            pass
        try:
            magVar = magnitude.xpath(".//mag/uncertainty")[0].text
            self.netMagVar = float(magVar)
        except:
            pass
        try:
            stacount = magnitude.xpath(".//stationCount")[0].text
            self.staMagCount = int(stacount)
        except:
            pass
        # get values
                # XXX the channel on which the S phase was picked is not
                # yet retrieved from the xml!
                # XXX information from synthetic phases is not yet
                # retrieved!!
            # append everything to the picklist
            #picklist.append([])
            #picklist[-1].extend([document_id, network, station, location])
            #picklist[-1].extend([channel, time, uncertainty, phaseHint])
            #picklist[-1].extend([onset, polarity, weight])
        #break #XXX using only first found event from given time span!!
        #return picklist


def main():
    usage = "USAGE: %prog -t <datetime> -d <duration> -i <channelids>"
    parser = OptionParser(usage)
    parser.add_option("-t", "--time", dest="time",
                      help="Starttime of seismogram to retrieve. It takes a "
                           "string which UTCDateTime can convert. "
                           "E.g. '2010-01-10T05:00:00'",
                      default='2009-07-21T04:33:00')
    parser.add_option("-d", "--duration", type="float", dest="duration",
                      help="Duration of seismogram in seconds",
                      default=120)
    parser.add_option("-i", "--ids", dest="ids",
                      help="Ids to retrieve, e.g. "
                           "'BW.RJOB..EH*,BW.RMOA..EH*'",
                      default='BW.RJOB..EH*,BW.RMOA..EH*')
    parser.add_option("-l", "--local", action="store_true", dest="local",
                      default=False,
                      help="use local files for design purposes")
    #parser.add_option("-k", "--keys", action="store_true", dest="keybindings",
    #                  default=False, help="Show keybindings and quit")
    (options, args) = parser.parse_args()
    for req in ['-d','-t','-i']:
        if not getattr(parser.values,parser.get_option(req).dest):
            parser.print_help()
            return
    
    #if options.keybindings:
    #    PickingGUI()
    #    for i in self.dictKeybindings.items():
    #        print i
    #    return

    if options.local:
        streams=[]
        streams.append(read('20091227_105240_Z.RJOB'))
        streams[0].append(read('20091227_105240_N.RJOB')[0])
        streams[0].append(read('20091227_105240_E.RJOB')[0])
        streams.append(read('20091227_105240_Z.RMOA'))
        streams[1].append(read('20091227_105240_N.RMOA')[0])
        streams[1].append(read('20091227_105240_E.RMOA')[0])
        streams.append(read('20091227_105240_Z.RNON'))
        streams[2].append(read('20091227_105240_N.RNON')[0])
        streams[2].append(read('20091227_105240_E.RNON')[0])
        streams.append(read('20091227_105240_Z.RTBE'))
        streams[3].append(read('20091227_105240_N.RTBE')[0])
        streams[3].append(read('20091227_105240_E.RTBE')[0])
        streams.append(read('20091227_105240_Z.RWMO'))
        streams[4].append(read('20091227_105240_N.RWMO')[0])
        streams[4].append(read('20091227_105240_E.RWMO')[0])
        #streams=[]
        #streams.append(read('RJOB_061005_072159.ehz.new'))
        #streams[0].append(read('RJOB_061005_072159.ehn.new')[0])
        #streams[0].append(read('RJOB_061005_072159.ehe.new')[0])
        #streams.append(read('RNON_160505_000059.ehz.new'))
        #streams.append(read('RMOA_160505_014459.ehz.new'))
        #streams[2].append(read('RMOA_160505_014459.ehn.new')[0])
        #streams[2].append(read('RMOA_160505_014459.ehe.new')[0])
    else:
        try:
            t = UTCDateTime(options.time)
            client = Client()
            streams = []
            for id in options.ids.split(","):
                net, sta, loc, cha = id.split(".")
                st = client.waveform.getWaveform(net, sta, loc, cha, 
                                                 t, t + options.duration)
                st.sort()
                st.reverse()
                streams.append(st)
        except:
            parser.print_help()
            raise

    PickingGUI(streams)

if __name__ == "__main__":
    main()
