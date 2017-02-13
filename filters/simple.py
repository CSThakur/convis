import litus
import theano
import theano.tensor as T
import numpy as np
import matplotlib.pylab as plt
from theano.tensor.nnet.conv3d2d import conv3d
from theano.tensor.signal.conv import conv2d
import uuid
from exceptions import NotImplementedError

from ..base import *
from ..theano_utils import make_nd, dtensor5
from .. import retina_base


class G_2d_recursive_filter(N):        
    def __init__(self,config={},name=None,model=None):
        """
            This node implements gauss filtering by two step recursion [see Deriche 92].

            The configuration accepts one of two parameters:

                * 'scalar_density': a homogeneous density of filtering
                * 'density_map': an inhomogeneous density map

            To convert $\sigma$ into density there will be some convenience functions.
        """
        self.set_config(config)
        dtensor4_broadcastable = T.TensorType('float64', (False,False,False,True))
        dtensor3_broadcastable = T.TensorType('float64', (False,False,True))
        a = as_parameter(dtensor4_broadcastable('a'), lambda x: (x.node.compute_config(x), x.node.a)[1])
        b = as_parameter(dtensor4_broadcastable('b'), lambda x: (x.node.compute_config(x), x.node.b)[1])
        _kx = as_parameter(T.iscalar("kx"), lambda x: x.input.shape[1]) # number of iteration steps in x direction
        _ky = as_parameter(T.iscalar("ky"), lambda x: x.input.shape[2]) # number of iteration steps in y direction
        def smooth_function_forward(L,a1,a2,b1,b2,Y1,Y2,I2):
            Y0 = a1 * L + a2 * I2 + b1 * Y1 + b2 * Y2
            return [Y0, Y1, L]
        def smooth_function_backward(L,Ybuf,a3,a4,b1,b2,R,Y1,Y2,I2):
            Y0 = a3 * L + a4 * I2 + b1 * Y1 + b2 * Y2
            return [Y0+Ybuf, Y0, Y1, L]
        # symbolic_input has dimensions (time,x,y)
        # to iterate over x dimension we swap to (x,y,time)
        L_shuffled_to_x_y_time = self.create_input().dimshuffle((1,2,0))
        result_forward_x, updates_forward_x = theano.scan(fn=smooth_function_forward,
                                      outputs_info = [L_shuffled_to_x_y_time[0]/2.0,
                                                      L_shuffled_to_x_y_time[0]/2.0,
                                                      L_shuffled_to_x_y_time[0]],
                                      sequences = [L_shuffled_to_x_y_time,
                                                   a[0],
                                                   a[1],
                                                   b[0],
                                                   b[1]],      
                                      n_steps=_kx,name='forward_pass_x')
        # we again iterate over x dimension, but in the reverse dimension
        result_backward_x, updates_backward_x = theano.scan(fn=smooth_function_backward,
                                      outputs_info = [L_shuffled_to_x_y_time[-1],
                                                      L_shuffled_to_x_y_time[-1]/2.0,
                                                      L_shuffled_to_x_y_time[-1]/2.0,
                                                      L_shuffled_to_x_y_time[-1]],
                                      sequences = [L_shuffled_to_x_y_time[::-1],
                                                   result_forward_x[0][::-1],
                                                   a[2][::-1],
                                                   a[3][::-1],
                                                   b[0][::-1],
                                                   b[1][::-1]],      
                                      n_steps=_kx,name='backward_pass_x')
        # result_backward_x has dimensions (x,y,time)
        # to iterate over y dimension we swap x and y to  (y,x,time)
        result_backward_x_shuffled_to_y_x_time = result_backward_x[0].dimshuffle((1,0,2))
        result_forward_y, updates_forward_y = theano.scan(fn=smooth_function_forward,
                                      outputs_info = [result_backward_x_shuffled_to_y_x_time[0,:,:]/2.0,
                                                      result_backward_x_shuffled_to_y_x_time[0,:,:]/2.0,
                                                      result_backward_x_shuffled_to_y_x_time[0,:,:]],
                                      sequences = [result_backward_x_shuffled_to_y_x_time,
                                                   a[0].dimshuffle((1,0,2)),
                                                   a[1].dimshuffle((1,0,2)),
                                                   b[0].dimshuffle((1,0,2)),
                                                   b[1].dimshuffle((1,0,2))],      
                                      n_steps=_ky,name='forward_pass_y')
        result_backward_y, updates_backward_y = theano.scan(fn=smooth_function_backward,
                                      outputs_info = [result_backward_x_shuffled_to_y_x_time[-1,:,:],
                                                      result_backward_x_shuffled_to_y_x_time[-1,:,:]/2.0,
                                                      result_backward_x_shuffled_to_y_x_time[-1,:,:]/2.0,
                                                      result_backward_x_shuffled_to_y_x_time[-1,:,:]],
                                      sequences = [result_backward_x_shuffled_to_y_x_time[::-1],
                                                   result_forward_y[0][::-1],
                                                   a[2].dimshuffle((1,0,2))[::-1],
                                                   a[3].dimshuffle((1,0,2))[::-1],
                                                   b[0].dimshuffle((1,0,2))[::-1],
                                                   b[1].dimshuffle((1,0,2))[::-1]],
                                      n_steps=_ky,name='backward_pass_y')
        update_variables = updates_forward_x + updates_backward_x + updates_forward_y + updates_backward_y
        output_variable = (result_backward_y[0].dimshuffle((2,1,0))[:,::-1,::-1]).reshape((result_backward_y[0].shape[2],result_backward_y[0].shape[1],result_backward_y[0].shape[0]))
        output_variable.name = 'output'
        super(G_2d_recursive_filter,self).__init__(output_variable,name=name)
        self.node_type = '2d Gauss Filter Node'
        self.node_description = lambda: 'Recursive Filtering'
    def compute_config(self,c):
            density = self.config.get('scalar_density',1.0) * np.ones(c.input.shape[1:])
            density = self.config.get('density_map',density)
            coeff = retina_base.deriche_coefficients(density)
            self.a = np.array([coeff[c][:,:,np.newaxis] for c in ['A1','A2','A3','A4']])
            self.b = np.array([coeff[c][:,:,np.newaxis] for c in ['B1','B2']])


class E_1d_recursive_filter(N):
    """
        This node implements temporal exponential filtering.

        The configuration accepts one parameter:

            * 'tau__sec': the time constant (in seconds relative to the model)

    """
    def __init__(self,config,name=None,model=None):
        self.set_config(config)
        self.model = model
        tau = as_parameter(theano.shared(model.seconds_to_steps(config.get('tau__sec',0.001))),
                           name = 'tau',
                           doc="""$\\tau$ gives the time constant of the exponential decay in seconds.
                           Small values give fast responses while large values give slow responses.
                           The steps to seconds conversion of the associated model will be used to compute.

                           The default value is 10ms.
                           """,
                           initialized = True,
                           optimizable = True,
                           config_key = 'tau__sec',
                           init=lambda x: x.node.get_model().seconds_to_steps(x.node.config.get('tau__sec',0.001)))
        steps = as_parameter(theano.shared(model.steps_to_seconds(1.0)),
                            name = 'step',
                            initialized = True, 
                            init=lambda x: x.node.get_model().steps_to_seconds(1.0))
        _preceding_V = as_state(T.dmatrix("preceding_V"),
                               init=lambda x: x.input[0,:,:]) # initial condition for sequence
        a_0 = 1.0
        a_1 = -T.exp(-steps/tau)
        b_0 = 1.0 - a_1
        _k = as_parameter(T.iscalar("k"),init=lambda x: x.input.shape[0]) # number of iteration steps
        def bipolar_step(input_image,
                        preceding_V):
            V = (input_image * b_0 - preceding_V * a_1) / a_0
            return V
        output_variable, _updates = theano.scan(fn=bipolar_step,
                                      outputs_info=[_preceding_V],
                                      sequences = [self.create_input()],
                                      non_sequences=[],
                                      n_steps=_k)
        output_variable.name = 'output'
        as_out_state(output_variable[-1],_preceding_V)
        super(E_1d_recursive_filter,self).__init__(output_variable,name=name)

E = E_1d_recursive_filter
G = G_2d_recursive_filter
RecursiveFilter1dExponential = E_1d_recursive_filter
RecursiveFilter2dGaussian = G_2d_recursive_filter

class G_2d_kernel_filter(N):        
    def __init__(self,config={},name=None,model=None):
        """
            This node implements 2d kernel filtering by convolution.
        """
        self.set_config(config)
        self.model = model
        self.size = self.config.get('kernel',self.config.get('size',(10,10))).shape
        kernel = as_parameter(theano.shared(self.config.get('kernel',np.zeros(self.config.get('size',(10,10)))),name='kernel'),
                             initialized=True,
                             init = lambda x: x.node.config.get('kernel',np.zeros(x.node.config.get('size',(10,10)))))
        output_variable = conv2d(pad3_txy(self.create_input(),0,kernel.shape[0]-1,kernel.shape[1]-1,mode='mirror'),kernel)
        output_variable.name = 'output'
        node_type = '2d Kernel Filter Node'
        node_description = lambda: 'Convolutional Filtering'
        super(G_2d_kernel_filter,self).__init__(output_variable,name=name)

class K_1d_kernel_filter(N):        
    def __init__(self,config={},name=None,model=None):
        """
            This node implements 2d kernel filtering by convolution.
        """
        self.set_config(config)
        self.model = model
        kernel = as_parameter(theano.shared(self.config.get('kernel',np.zeros(self.config.get('size',(10)))),name='kernel'),
                             initialized=True,
                             init = lambda x: x.node.config.get('kernel',np.zeros(x.node.config.get('size',(10)))))
        self.size = kernel.shape
        N = kernel.shape[0]
        output_variable = make_nd(conv3d(pad5(make_nd(self.create_input(),5),N,1),make_nd(kernel,5)),3)
        output_variable.name = 'output'
        node_type = '1d Kernel Filter Node'
        node_description = lambda: 'Convolutional Filtering'
        super(K_1d_kernel_filter,self).__init__(output_variable,name=name)

class K_3d_kernel_filter(N):        
    def __init__(self,config={},name=None,model=None):
        """
            This node implements 3d kernel filtering by convolution.
        """
        self.set_config(config)
        self.model = model
        kernel = as_parameter(theano.shared(self.config.get('kernel',np.zeros(self.config.get('size',(10,10,10)))),name='kernel'),
                             initialized=True,
                             init = lambda x: x.node.config.get('kernel',np.zeros(x.node.config.get('size',(10,10,10)))))
        self.size = kernel.shape
        output_variable = make_nd(
                              conv3d(
                                  pad5_txy(
                                              make_nd(self.create_input(),5),
                                              kernel.shape[0]-1,kernel.shape[1]-1,kernel.shape[2]-1,
                                              mode='mirror'
                                          ),
                                  make_nd(kernel,5)
                              )
                          ,3)
        output_variable.name = 'output'
        node_type = '3d Kernel Filter Node'
        node_description = lambda: 'Convolutional Filtering'
        super(K_3d_kernel_filter,self).__init__(output_variable,name=name)

ConvolutionFilter1d = K_1d_kernel_filter
ConvolutionFilter2d = G_2d_kernel_filter
ConvolutionFilter3d = K_3d_kernel_filter

class MaxFilter(N):        
    def __init__(self,config={},name=None,model=None):
        """
            This gives the (spatial) maximum of the input for each time step.
        """
        self.set_config(config)
        self.model = model
        output_variable = T.max(self.create_input(),(1,2))
        output_variable.name = 'output'
        node_type = 'Spatial Max Filter'
        node_description = lambda: ''
        #raise NotImplementedError('Filter is not defined yet')
        super(MaxFilter,self).__init__(output_variable,name=name)

class MinFilter(N):        
    def __init__(self,config={},name=None,model=None):
        """
            This gives the (spatial) maximum of the input for each time step.
        """
        self.set_config(config)
        self.model = model
        output_variable = T.min(self.create_input(),(1,2))
        output_variable.name = 'output'
        node_type = 'Spatial Max Filter'
        node_description = lambda: ''
        #raise NotImplementedError('Filter is not defined yet')
        super(MinFilter,self).__init__(output_variable,name=name)

class SelectPixelFilter(N):        
    def __init__(self,config={},name=None,model=None):
        """
            This gives the (spatial) maximum of the input for each time step.
        """
        self.set_config(config)
        self.model = model
        self._x = self.shared_parameter(
                lambda x: int(x.value_from_config()),
                config_key = 'x',
                config_default = 0,
                doc='x index of selection',
                name = "x")
        self._y = self.shared_parameter(
                lambda x: int(x.value_from_config()),
                config_key = 'y',
                config_default = 0,
                doc='y index of selection',
                name = "y")
        output_variable = self.create_input()[:,self._x,self._y]
        output_variable.name = 'output'
        #raise NotImplementedError('Filter is not defined yet')
        super(SelectPixelFilter,self).__init__(output_variable,name=name)
        self.node_type = 'Spatial Selection Filter'
        self.node_description = lambda: ''

class SoftSelectFilter(N):        
    def __init__(self,config={},name=None,model=None):
        """
            This gives the (spatial) maximum of the input for each time step.
        """
        self.set_config(config)
        self.model = model
        self.a = self.shared_parameter(
                lambda x: x.value_from_config(),
                config_key = 'a',
                config_default = 0.5*np.ones((1,1)),
                doc='Ratio of input a in the output. Input b will have ratio (1-`a`)',
                name = "a")
        inputs = self.create_input(['input_a','input_b'])
        output_variable = self.a.dimshuffle(('x',0,1)) * inputs['input_a'] + (1.0-self.a.dimshuffle(('x',0,1))) * inputs['input_b']
        output_variable.name = 'output'
        #raise NotImplementedError('Filter is not defined yet')
        super(SoftSelectFilter,self).__init__(output_variable,name=name)
        self.node_type = 'Soft Select Filter'
        self.node_description = lambda: ''

class RF_2d_kernel_filter(N):
    def __init__(self,config={},name=None,model=None):
        """
            This node implements 2d kernel filtering by convolution.
        """
        self.set_config(config)
        self.model = model
        self.size = self.config.get('kernel',self.config.get('size',(10,10))).shape
        kernel = as_parameter(theano.shared(self.config.get('kernel',self.config.get('size',(10,10))),name='kernel'),
                             initialized=True,
                             init = lambda x: x.node.config.get('kernel',np.zeros(x.node.config.get('size',(10,10)))))
        output_variable = self.create_input()[:,:kernel.shape[0],:kernel.shape[1]] * kernel.dimshuffle(('x',0,1))
        output_variable.name = 'output'
        node_type = '2d Single RF Node'
        node_description = lambda: 'Multiplicative Mask'
        super(RF_2d_kernel_filter,self).__init__(output_variable,name=name)

class RF_3d_kernel_filter(N):
    def __init__(self,config={},name=None,model=None):
        """
            This node implements 3d kernel filtering by convolution.

            The spatial dimensions are fixed to a reference frame, the temporal dimension is convolved.
        """
        self.set_config(config)
        self.model = model
        kernel = as_parameter(theano.shared(self.config.get('kernel',np.zeros(self.config.get('size',(10,10,10)))),name='kernel'),
                             initialized=True,
                             init = lambda x: x.node.config.get('kernel',np.zeros(x.node.config.get('size',(10,10,10)))))
        self.size = kernel.shape
        output_variable = make_nd(
                              conv3d(
                                  pad5_txy(
                                              make_nd(self.create_input(),5),
                                              kernel.shape[0]-1,0,0,
                                              mode='mirror'
                                          ),
                                  make_nd(kernel,5)
                              )
                          ,3)
        output_variable.name = 'output'
        node_type = '3d Single RF Node'
        node_description = lambda: 'Mask * Temporal Convolution'
        super(RF_3d_kernel_filter,self).__init__(output_variable,name=name)

class T_1d_filter_with_sigma_param(N):
    """
        This node implements temporal exponential filtering.

        The configuration accepts one parameter:

            * 'tau__sec': the time constant (in seconds relative to the model)

    """
    def __init__(self,config,name=None,model=None):
        self.set_config(config)
        self.model = model
        tau = as_parameter(theano.shared(model.seconds_to_steps(config.get('tau__sec',0.001))),
                           name = 'tau',
                           doc="""$\\tau$ gives the time constant of the exponential decay in seconds.
                           Small values give fast responses while large values give slow responses.
                           The steps to seconds conversion of the associated model will be used to compute.

                           The default value is 10ms.
                           """,
                           initialized = True,
                           optimizable = True,
                           config_key = 'tau__sec',
                           init=lambda x: x.node.get_model().seconds_to_steps(x.node.config.get('tau__sec',0.001)))
        steps = as_parameter(theano.shared(model.steps_to_seconds(1.0)),
                            name = 'step',
                            initialized = True, 
                            init=lambda x: x.node.get_model().steps_to_seconds(1.0))
        _preceding_V = as_state(T.dmatrix("preceding_V"),
                               init=lambda x: x.input[0,:,:]) # initial condition for sequence
        _preceding_input = as_state(T.dmatrix("preceding_input"),
                               init=lambda x: x.input[0,:,:]) # initial condition for sequence
        a_0 = 1.0
        a_1 = -T.exp(-steps/tau)
        b_0 = 1.0 - a_1
        _k = as_parameter(T.iscalar("k"),init=lambda x: x.input.shape[0]) # number of iteration steps
        def bipolar_step(input_image,
                        preceding_V,preceding_input):
            V = input_image - 0.1*(preceding_input * b_0 - preceding_V * a_1) / a_0
            return V,input_image
        inp = self.create_input()
        output_variable, _updates = theano.scan(fn=bipolar_step,
                                      outputs_info=[_preceding_V,_preceding_input],
                                      sequences = [inp],
                                      non_sequences=[],
                                      n_steps=_k)
        output_variable[0].name = 'output'
        as_out_state(output_variable[0][-1],_preceding_V)
        as_out_state(inp[-1],_preceding_input)
        super(T_1d_filter_with_sigma_param,self).__init__(output_variable[0],name=name)

RecursiveFilter1dExponentialHighPass = T_1d_filter_with_sigma_param
    
class RecursiveLeakyHeatFilter(N):        
    def __init__(self,config={},name=None,model=None):
        """
            This node implements gauss filtering by two step recursion [see Deriche 92].

            The configuration accepts one of two parameters:

                * 'scalar_density': a homogeneous density of filtering
                * 'density_map': an inhomogeneous density map

            To convert $\sigma$ into density there will be some convenience functions.
        """
        self.set_config(config)
        raise NotImplementedError('This class is not yet implemented. Use `LeakyHeatFilter` for now.')
        tau = as_parameter(theano.shared(model.seconds_to_steps(config.get('tau__sec',0.001))),
                           doc="""$\\tau_{sec}$ gives the time constant of the exponential decay in seconds.
                           Small values give fast responses while large values give slow responses.
                           The steps to seconds conversion of the associated model will be used to compute.

                           The default value is 10ms.
                           """,
                           initialized = True,
                           optimizable = True,
                           config_key = 'tau__sec',
                           init=lambda x: x.node.get_model().seconds_to_steps(x.node.config.get('tau__sec',0.001)))
        steps = as_parameter(theano.shared(model.steps_to_seconds(1.0)),
                            name = 'step',
                            initialized = True, 
                            init=lambda x: x.node.get_model().steps_to_seconds(1.0))
        _preceding_V = as_state(T.dmatrix("preceding_V"),
                               init=lambda x: x.input[0,:,:]) # initial condition for sequence
        _preceding_input = as_state(T.dmatrix("preceding_input"),
                               init=lambda x: x.input[0,:,:]) # initial condition for sequence
        a_0 = 1.0
        a_1 = -T.exp(-steps/tau)
        b_0 = 1.0 - a_1
        _k = as_parameter(T.iscalar("k"),init=lambda x: x.input.shape[0]) # number of iteration steps

        ## radial blur
        dtensor4_broadcastable = T.TensorType('float64', (False,False,False,True))
        dtensor3_broadcastable = T.TensorType('float64', (False,False,True))
        a = as_parameter(dtensor4_broadcastable('a'), lambda x: (x.node.compute_config(x), x.node.a)[1])
        b = as_parameter(dtensor4_broadcastable('b'), lambda x: (x.node.compute_config(x), x.node.b)[1])
        _kx = as_parameter(T.iscalar("kx"), lambda x: x.input.shape[1]) # number of iteration steps in x direction
        _ky = as_parameter(T.iscalar("ky"), lambda x: x.input.shape[2]) # number of iteration steps in y direction
        def smooth_function_forward(L,a1,a2,b1,b2,Y1,Y2,I2):
            Y0 = a1 * L + a2 * I2 + b1 * Y1 + b2 * Y2
            return [Y0, Y1, L]
        def smooth_function_backward(L,Ybuf,a3,a4,b1,b2,R,Y1,Y2,I2):
            Y0 = a3 * L + a4 * I2 + b1 * Y1 + b2 * Y2
            return [Y0+Ybuf, Y0, Y1, L]

        def smoothing_fb_pass(L, _a, _b, dim_str='x'):
            result_forward, updates_ = theano.scan(fn=smooth_function_forward,
                                          outputs_info = [L[0]/2.0, L[0]/2.0,L[0]],
                                          sequences = [L,_a[0],_a[1],_b[0],_b[1]],
                                          n_steps=_kx,name='forward_pass_'+dim_str)
            print a,b
            result_backward, updates_backward = theano.scan(fn=smooth_function_backward,
                                          outputs_info = [L[-1],L[-1]/2.0,L[-1]/2.0,L[-1]],
                                          sequences = [L[::-1],result_forward[0][::-1],_a[2][::-1],_a[3][::-1],_b[0][::-1],_b[1][::-1]],
                                          n_steps=_kx,name='backward_pass_'+dim_str)            
            return result_backward
        
        def bipolar_step(input_image,
                        preceding_V,preceding_input):
            V = input_image - 0.1*(preceding_input * b_0 - preceding_V * a_1) / a_0
            L_shuffled_to_x_y_time = V.dimshuffle((1,0,-1)) # adding a dimension for time
            result_backward_x = smoothing_fb_pass(L_shuffled_to_x_y_time, a, b, 'x')
            result_backward_x_shuffled_to_y_x_time = result_backward_x[0].dimshuffle((1,0,2))
            result_backward_y = smoothing_fb_pass(result_backward_x_shuffled_to_y_x_time, [a[0].dimshuffle((1,0,2)),a[1].dimshuffle((1,0,2)),a[2].dimshuffle((1,0,2)),a[3].dimshuffle((1,0,2))], [b[0].dimshuffle((1,0,2)),b[1].dimshuffle((1,0,2))], 'x')
            V_smoothed = (result_backward_y[0].dimshuffle((2,1,0)))#.reshape((result_backward_y[0].shape[2],result_backward_y[0].shape[1],result_backward_y[0].shape[0]))
            return V_smoothed[0],input_image
        
        inp = self.create_input()
        output_variable, _updates = theano.scan(fn=bipolar_step,
                                      outputs_info=[_preceding_V,_preceding_input],
                                      sequences = [inp],
                                      non_sequences=[],
                                      n_steps=_k)
        output_variable[0].name = 'output'
        as_out_state(output_variable[0][-1],_preceding_V)
        as_out_state(inp[-1],_preceding_input)
        super(RecursiveLeakyHeatFilter,self).__init__(output_variable[0],name=name)
        self.node_type = 'Leaky Heat Filter Node'
        self.node_description = lambda: 'Double Recursive Filtering'
    def compute_config(self,c):
            density = self.config.get('scalar_density',1.0) * np.ones(c.input.shape[1:])
            density = self.config.get('density_map',density)
            coeff = retina_base.deriche_coefficients(density)
            self.a = np.array([coeff[c][:,:,np.newaxis] for c in ['A1','A2','A3','A4']])
            self.b = np.array([coeff[c][:,:,np.newaxis] for c in ['B1','B2']])
        

class LeakyHeatFilter(N):        
    def __init__(self,config={},name=None,model=None):
        """
            This node implements gauss filtering by two step recursion [see Deriche 92].

            The configuration accepts one of two parameters:

                * 'scalar_density': a homogeneous density of filtering
                * 'density_map': an inhomogeneous density map

            To convert $\sigma$ into density there will be some convenience functions.
        """
        self.set_config(config)
        tau = as_parameter(theano.shared(model.seconds_to_steps(config.get('tau',0.001))),
                           name = 'tau',
                           doc="""$\\tau$ gives the time constant of the exponential decay in seconds.
                           Small values give fast responses while large values give slow responses.
                           The steps to seconds conversion of the associated model will be used to compute.

                           The default value is 10ms.
                           """,
                           initialized = True,
                           optimizable = True,
                           config_key = 'tau',
                           init=lambda x: x.node.get_model().seconds_to_steps(x.node.config.get('tau',0.001)))
        steps = as_parameter(theano.shared(model.steps_to_seconds(1.0)),
                            name = 'step',
                            doc="""To convert the time constant in seconds into the appropriate length in bins or steps, this value will be automatically filled via the associatated model.""",
                            initialized = True, 
                            init=lambda x: x.node.get_model().steps_to_seconds(1.0))
        _preceding_V = as_state(T.dmatrix("preceding_V"),
                               doc="Since recursive filtering needs the result of the previous timestep, the last time step has to be remembered as a state inbetween computations.",
                               init=lambda x: x.input[0,:,:]) # initial condition for sequence
        _preceding_input = as_state(T.dmatrix("preceding_input"),
                               init=lambda x: x.input[0,:,:]) # initial condition for sequence
        a_0 = 1.0
        a_1 = -T.exp(-steps/tau)
        self.a_1 = a_1
        b_0 = 1.0 - a_1
        _k = as_parameter(T.iscalar("k"),init=lambda x: x.input.shape[0]) # number of iteration steps

        ## radial blur
        dtensor4_broadcastable = T.TensorType('float64', (False,False,False,True))
        dtensor3_broadcastable = T.TensorType('float64', (False,False,True))

        kernel = as_parameter(theano.shared(self.config.get('kernel',self.config.get('size',(10,10))),name='kernel'),
                             initialized=True,
                             config_key = 'kernel',
                             doc = """Holds a dense matrix that is to be used as a 2d smoothing kernel.

                             A gaussian kernel for this variable can be created using `retina_base.m_g_filter_2d`.

                             The filter can be optimized.
                             """,
                             init = lambda x: x.node.config.get('kernel',np.zeros(x.node.config.get('size',(10,10)))))

        
        def filter_step(input_image,
                        preceding_V,preceding_input):
            """
                This function computes a single frame for the recursive exponential filtering.

                Additionally, in each step the output is smoothed with a kernel, such that
                activity propagates across the entire population (if given enough time).
            """
            #V = input_image - 0.1*(preceding_input * b_0 - preceding_V * a_1) / a_0
            #V = preceding_V + input_image# + 0.01*(preceding_input * b_0 - preceding_V * a_1) / a_0
            V = (input_image * b_0 - preceding_V * a_1) / a_0

            s0 = (kernel.shape[0]-1)/2
            s0end = V.shape[0] + s0
            s1 = (kernel.shape[1]-1)/2
            s1end = V.shape[1] + s1
            V_smoothed = theano.tensor.signal.conv.conv2d(V,kernel, border_mode='full')[s0:s0end,s1:s1end]
            return V_smoothed,input_image
        
        inp = self.create_input()
        output_variable, _updates = theano.scan(fn=filter_step,
                                      outputs_info=[_preceding_V,_preceding_input],
                                      sequences = [inp],
                                      non_sequences=[],
                                      n_steps=_k)
        output_variable[0].name = 'output'
        as_out_state(output_variable[0][-1],_preceding_V)
        as_out_state(inp[-1],_preceding_input)
        super(LeakyHeatFilter,self).__init__(output_variable[0],name=name)
        self.node_type = 'Leaky Heat Filter Node'
        self.node_description = lambda: 'Temporal Recursive Filtering and Spatial Convolution'
        
